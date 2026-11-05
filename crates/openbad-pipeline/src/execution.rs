//! Execution decisions: who runs, how many at once, and when a turn is over.
//!
//! Nothing here starts a process or opens a session. These are the rules a host
//! applies around a turn, kept pure so they can be tested without a worktree, a
//! model, or a clock — including the cases that need a crash or a timeout to
//! reproduce for real.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::ticket::{AssignmentState, ReportOutcome, TicketState};

/// How long a prepare command may run before it is killed.
///
/// Bounded because a prepare that never returns would hold its ticket forever.
/// Ten minutes is the prototype's allowance, and enough for a dependency install
/// on a slow connection; a prepare that takes longer than that is a problem the
/// ticket should report rather than one it should wait out.
pub const PREPARE_TIMEOUT: Duration = Duration::from_secs(600);

/// Everything one ticket's executor needs before its turn starts.
///
/// The names and the variables are decided here rather than assembled at the call
/// site, so "one session per ticket, in its own directory, on its own branch"
/// holds by construction and can be asserted without a worktree or a model.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchPlan {
    /// `worker-<ticket>`: the name the ticket's session is known by, reused across
    /// its assignments so the executor keeps its history (ADR-009).
    pub session: String,
    /// The branch the worktree is created on. Never a protected branch: the
    /// executor pushes this one and opens a pull request.
    pub branch: String,
    /// One directory per ticket, under the project's execution root.
    pub directory: PathBuf,
    /// Handed to the prepare command and the executor's instructions verbatim.
    ///
    /// The key type is a closed set, which is the point: a caller cannot smuggle
    /// a credential in beside the identifiers, and the test asserts none of the
    /// values is one.
    pub variables: BTreeMap<Variable, String>,
}

/// The variables a launch passes on. Closed on purpose; see [`LaunchPlan`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Variable {
    Ticket,
    Source,
    Thread,
    Repository,
    Project,
    Worktree,
    Branch,
    Mandate,
}

impl Variable {
    /// The name the prepare command and the instructions see.
    #[must_use]
    pub const fn name(self) -> &'static str {
        match self {
            Self::Ticket => "OPENBAD_TICKET",
            Self::Source => "OPENBAD_SOURCE",
            Self::Thread => "OPENBAD_THREAD",
            Self::Repository => "OPENBAD_REPOSITORY",
            Self::Project => "OPENBAD_PROJECT",
            Self::Worktree => "OPENBAD_WORKTREE",
            Self::Branch => "OPENBAD_BRANCH",
            Self::Mandate => "OPENBAD_MANDATE",
        }
    }
}

/// Plans one ticket's executor: its session, its branch, its directory, and the
/// variables both its prepare command and its instructions are given.
#[must_use]
pub fn launch_plan(
    execution_root: &Path,
    project_root: &Path,
    ticket_id: &str,
    source_id: &str,
    thread: &str,
    repository: &str,
    mandate_id: &str,
) -> LaunchPlan {
    let ticket = safe_component(ticket_id);
    let directory = execution_root.join(&ticket);
    let branch = format!("worker/{ticket}");
    let session = format!("worker-{ticket}");

    let mut variables = BTreeMap::new();
    variables.insert(Variable::Ticket, ticket);
    variables.insert(Variable::Source, source_id.to_owned());
    variables.insert(Variable::Thread, thread.to_owned());
    variables.insert(Variable::Repository, repository.to_owned());
    variables.insert(Variable::Project, project_root.display().to_string());
    variables.insert(Variable::Worktree, directory.display().to_string());
    variables.insert(Variable::Branch, branch.clone());
    variables.insert(Variable::Mandate, mandate_id.to_owned());

    LaunchPlan {
        session,
        branch,
        directory,
        variables,
    }
}

/// What a ticket's executor is told, in the terms the ADR uses.
///
/// The instructions carry the two things a mandate does *not* buy, so the
/// executor reads them as part of the job rather than discovering them as a
/// refusal: destructive commands outside the worktree stay refused, and the
/// branch is pushed for review rather than into a protected branch.
#[must_use]
pub fn ticket_prompt(plan: &LaunchPlan, mandate: &crate::mandate::Mandate) -> String {
    let thread = plan
        .variables
        .get(&Variable::Thread)
        .cloned()
        .unwrap_or_default();
    let repository = plan
        .variables
        .get(&Variable::Repository)
        .cloned()
        .unwrap_or_default();

    format!(
        "You are the executor for ticket {ticket} on {repository}, working on thread {thread}.\n\
         \n\
         Mandate: {strength:?} authority, granted by {requester} via {received_via} under policy \
         version {policy}. Scope: {scope}. Matched rules: {rules}.\n\
         \n\
         Your worktree is {worktree}, on branch {branch}. Work only there. You have full access \
         inside it and are not asked to approve each step: that is what the mandate buys.\n\
         \n\
         Two things are still refused, deliberately:\n\
         - Destructive commands outside the worktree, and writes outside it, are not authorized \
         by any mandate. Use the worktree or scratch space.\n\
         - Never push to a protected branch. Push {branch} and open a pull request for review.\n\
         \n\
         Before you report, put the branch on top of the current base: `git fetch origin`, then \
         rebase onto it, resolve any conflicts yourself, re-run the tests, and push. Conflicts are \
         your work, not a person's — a branch that cannot merge is an unfinished ticket.\n\
         \n\
         Read the thread with `gh` for the request, do the work, verify it, and then finish with a \
         report: what changed, what you checked, and what remains risky. Do not ask a person a \
         question — nobody is watching this turn; if you cannot proceed, say so in the report and \
         the ticket waits for someone.",
        ticket = plan
            .variables
            .get(&Variable::Ticket)
            .cloned()
            .unwrap_or_default(),
        strength = mandate.strength,
        requester = mandate.requester,
        received_via = mandate.received_via,
        policy = mandate.policy_version,
        scope = mandate.scope,
        rules = if mandate.matched_rules.is_empty() {
            "none recorded".to_owned()
        } else {
            mandate.matched_rules.join(", ")
        },
        worktree = plan.directory.display(),
        branch = plan.branch,
    )
}

/// A ticket id from the store is already a uuid, but it becomes a path and a
/// branch here, so anything that could leave the execution root is neutralized
/// before it is used.
fn safe_component(raw: &str) -> String {
    let cleaned: String = raw
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || character == '-' || character == '_' {
                character
            } else {
                '-'
            }
        })
        .collect();
    if cleaned.is_empty() {
        "ticket".to_owned()
    } else {
        cleaned
    }
}

/// Who asked for a launch. Decides the order candidates wait in, not whether they
/// are allowed — admission already settled that.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SlotPriority {
    /// A person wrote on one of the ticket's threads. Their turn takes the next
    /// free slot before any new work starts, so a queue of new tickets cannot
    /// keep a person waiting behind it.
    Person,
    /// New work, or a wake from a check result, a review, or a timer.
    Background,
}

/// Which executors are running, and in whose name.
///
/// Capacity is a project setting and is never exceeded. The prototype keeps one
/// extra slot for a person so they are never blocked; that is deliberately not
/// done here, because a bound that can be exceeded is not a bound — a person's
/// message is ordered ahead of new work instead, which keeps them first without
/// breaking the configured limit.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Slots {
    capacity: u32,
    holders: Vec<Holder>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Holder {
    ticket_id: String,
    priority: SlotPriority,
}

impl Slots {
    /// Creates the accounting for `capacity` concurrent executors.
    ///
    /// A capacity of zero is treated as one: a pipeline configured to run nothing
    /// would silently never work, and "off" already has a spelling — a disabled
    /// source, or no pipeline file at all.
    #[must_use]
    pub fn new(capacity: u32) -> Self {
        Self {
            capacity: capacity.max(1),
            holders: Vec::new(),
        }
    }

    #[must_use]
    pub const fn capacity(&self) -> u32 {
        self.capacity
    }

    #[must_use]
    pub fn in_use(&self) -> u32 {
        self.holders.len() as u32
    }

    /// Whether a ticket already holds a slot.
    #[must_use]
    pub fn holds(&self, ticket_id: &str) -> bool {
        self.holders
            .iter()
            .any(|holder| holder.ticket_id == ticket_id)
    }

    /// Takes a slot for a ticket, if one is free and it has none.
    ///
    /// A ticket holds at most one slot, because it runs at most one assignment.
    /// Returns whether the ticket now holds a slot.
    pub fn try_acquire(&mut self, ticket_id: &str, priority: SlotPriority) -> bool {
        if self.holds(ticket_id) {
            return true;
        }
        if self.in_use() >= self.capacity {
            return false;
        }
        self.holders.push(Holder {
            ticket_id: ticket_id.to_owned(),
            priority,
        });
        true
    }

    /// Releases a ticket's slot. Returns whether it held one.
    pub fn release(&mut self, ticket_id: &str) -> bool {
        let before = self.holders.len();
        self.holders.retain(|holder| holder.ticket_id != ticket_id);
        self.holders.len() != before
    }

    /// The tickets holding a slot, people's turns first.
    #[must_use]
    pub fn running(&self) -> Vec<&str> {
        let mut held: Vec<&Holder> = self.holders.iter().collect();
        held.sort_by_key(|holder| match holder.priority {
            SlotPriority::Person => 0,
            SlotPriority::Background => 1,
        });
        held.into_iter()
            .map(|holder| holder.ticket_id.as_str())
            .collect()
    }
}

/// A ticket waiting for a slot.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Candidate {
    pub ticket_id: String,
    pub priority: SlotPriority,
}

/// The order waiting tickets should be launched in as slots free up.
///
/// Stable within a priority, so a ticket that has been waiting longest is not
/// overtaken by one that arrived later at the same priority — otherwise a busy
/// project could starve the same ticket indefinitely.
#[must_use]
pub fn launch_order(candidates: &[Candidate]) -> Vec<&Candidate> {
    let mut ordered: Vec<&Candidate> = candidates.iter().collect();
    ordered.sort_by_key(|candidate| match candidate.priority {
        SlotPriority::Person => 0,
        SlotPriority::Background => 1,
    });
    ordered
}

/// What a ticket may spend on one assignment.
///
/// Zero means no limit. A limit exists so an unattended loop cannot run forever,
/// not to keep work small: a project that wants a budget says so.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Budget {
    pub steps: u64,
    pub tokens: u64,
}

impl Budget {
    /// No limit on either axis.
    pub const UNLIMITED: Self = Self {
        steps: 0,
        tokens: 0,
    };

    /// Whether both axes are unlimited.
    #[must_use]
    pub const fn is_unlimited(self) -> bool {
        self.steps == 0 && self.tokens == 0
    }

    /// Which limit an assignment has reached, if any.
    ///
    /// Steps are checked before tokens so the reason a ticket stopped is the
    /// first thing that ran out, and the numbers travel with it — a failure that
    /// says only "budget exceeded" leaves a person guessing.
    #[must_use]
    pub const fn reached(self, spent: Spent) -> Option<Limit> {
        if self.steps != 0 && spent.steps >= self.steps {
            return Some(Limit::Steps {
                allowed: self.steps,
                spent: spent.steps,
            });
        }
        if self.tokens != 0 && spent.tokens >= self.tokens {
            return Some(Limit::Tokens {
                allowed: self.tokens,
                spent: spent.tokens,
            });
        }
        None
    }
}

/// What an assignment has spent.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Spent {
    pub steps: u64,
    pub tokens: u64,
}

/// Which budget ran out, and by how much.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Limit {
    Steps { allowed: u64, spent: u64 },
    Tokens { allowed: u64, spent: u64 },
}

impl Limit {
    /// Why the ticket failed, in words a person can act on.
    #[must_use]
    pub fn reason(self) -> String {
        match self {
            Self::Steps { allowed, spent } => {
                format!("stopped after {spent} steps; the ticket allows {allowed}")
            }
            Self::Tokens { allowed, spent } => {
                format!("stopped after {spent} tokens; the ticket allows {allowed}")
            }
        }
    }
}

/// What a report does to the assignment, the ticket, and the slot.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Effects {
    /// Whether this report concludes the assignment's turn.
    pub end_assignment: bool,
    /// The ticket's new state, when the report moves it.
    pub ticket: Option<TicketState>,
    /// Whether the ticket's slot is free afterwards.
    pub release_slot: bool,
}

/// The effects of a report.
///
/// A final report always ends the assignment and frees the slot: an assignment
/// that has reported a final outcome is not working, and leaving the slot held
/// would keep the next ticket waiting for nothing. `progress` does neither.
#[must_use]
pub const fn effects_of(outcome: ReportOutcome) -> Effects {
    if !outcome.is_final() {
        return Effects {
            end_assignment: false,
            ticket: None,
            release_slot: false,
        };
    }
    Effects {
        end_assignment: true,
        ticket: if outcome.ends_ticket() {
            Some(TicketState::Ended)
        } else {
            None
        },
        release_slot: true,
    }
}

/// What to do about an assignment that may or may not still be running.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Recovery {
    /// It is where it should be. Leave it.
    Leave,
    /// Start it again: it never got going.
    Relaunch,
    /// End it and start a new one: its session is gone.
    EndAndRelaunch,
}

/// What to do about an assignment after a restart or a lost session.
///
/// The two harms are different. Relaunching something that is already running
/// gives a ticket two executors; leaving a dead assignment as `running` leaves the
/// ticket stuck forever. So "is a session alive?" decides, not elapsed time.
#[must_use]
pub const fn recovery_for(state: AssignmentState, session_alive: bool) -> Recovery {
    match (state, session_alive) {
        (AssignmentState::Ended, _) => Recovery::Leave,
        (AssignmentState::Pending, false) => Recovery::Relaunch,
        (AssignmentState::Pending, true) | (AssignmentState::Running, true) => Recovery::Leave,
        (AssignmentState::Running, false) => Recovery::EndAndRelaunch,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_ticket_holds_at_most_one_slot() {
        let mut slots = Slots::new(2);
        assert!(slots.try_acquire("t-1", SlotPriority::Background));
        assert!(slots.try_acquire("t-1", SlotPriority::Background));
        assert_eq!(slots.in_use(), 1);
    }

    #[test]
    fn capacity_is_never_exceeded_even_for_a_person() {
        let mut slots = Slots::new(1);
        assert!(slots.try_acquire("t-1", SlotPriority::Background));
        assert!(!slots.try_acquire("t-2", SlotPriority::Person));
        assert_eq!(slots.in_use(), 1);
    }

    #[test]
    fn a_person_is_ordered_before_new_work_without_breaking_the_capacity() {
        let waiting = [
            Candidate {
                ticket_id: "new-1".to_owned(),
                priority: SlotPriority::Background,
            },
            Candidate {
                ticket_id: "person-1".to_owned(),
                priority: SlotPriority::Person,
            },
        ];
        let order: Vec<&str> = launch_order(&waiting)
            .iter()
            .map(|candidate| candidate.ticket_id.as_str())
            .collect();
        assert_eq!(order, ["person-1", "new-1"]);
        assert_eq!(Slots::new(1).capacity(), 1, "a person gets no extra slot");
    }

    #[test]
    fn waiting_tickets_keep_their_order_within_a_priority() {
        let waiting = [
            Candidate {
                ticket_id: "a".to_owned(),
                priority: SlotPriority::Background,
            },
            Candidate {
                ticket_id: "b".to_owned(),
                priority: SlotPriority::Background,
            },
            Candidate {
                ticket_id: "c".to_owned(),
                priority: SlotPriority::Background,
            },
        ];
        let order: Vec<&str> = launch_order(&waiting)
            .iter()
            .map(|candidate| candidate.ticket_id.as_str())
            .collect();
        assert_eq!(order, ["a", "b", "c"]);
    }

    #[test]
    fn a_zero_capacity_still_runs_one_at_a_time() {
        let mut slots = Slots::new(0);
        assert_eq!(slots.capacity(), 1);
        assert!(slots.try_acquire("t-1", SlotPriority::Background));
        assert!(!slots.try_acquire("t-2", SlotPriority::Background));
    }

    #[test]
    fn releasing_a_slot_that_was_never_held_changes_nothing() {
        let mut slots = Slots::new(1);
        assert!(!slots.release("t-1"));
        assert_eq!(slots.in_use(), 0);
    }

    #[test]
    fn no_budget_means_no_limit_however_much_is_spent() {
        let budget = Budget::UNLIMITED;
        assert!(budget.is_unlimited());
        assert_eq!(
            budget.reached(Spent {
                steps: u64::MAX,
                tokens: u64::MAX
            }),
            None
        );
    }

    #[test]
    fn a_step_limit_reports_what_it_allowed_and_what_was_spent() {
        let budget = Budget {
            steps: 5,
            tokens: 0,
        };
        assert_eq!(
            budget.reached(Spent {
                steps: 5,
                tokens: 0
            }),
            Some(Limit::Steps {
                allowed: 5,
                spent: 5
            })
        );
        assert_eq!(
            budget.reached(Spent {
                steps: 4,
                tokens: 0
            }),
            None
        );
    }

    #[test]
    fn steps_are_reported_before_tokens_when_both_run_out() {
        let budget = Budget {
            steps: 1,
            tokens: 1,
        };
        let limit = budget
            .reached(Spent {
                steps: 9,
                tokens: 9,
            })
            .expect("a limit");
        assert_eq!(
            limit.reason(),
            "stopped after 9 steps; the ticket allows 1",
            "the first thing that ran out explains the stop"
        );
    }

    #[test]
    fn progress_leaves_the_assignment_and_the_ticket_alone() {
        assert_eq!(
            effects_of(ReportOutcome::Progress),
            Effects {
                end_assignment: false,
                ticket: None,
                release_slot: false
            }
        );
    }

    #[test]
    fn a_final_report_ends_the_assignment_and_frees_the_slot() {
        for outcome in [
            ReportOutcome::Waiting,
            ReportOutcome::Ready,
            ReportOutcome::Blocked,
            ReportOutcome::Failed,
            ReportOutcome::Dropped,
        ] {
            let effects = effects_of(outcome);
            assert!(effects.end_assignment, "{outcome:?}");
            assert!(effects.release_slot, "{outcome:?}");
        }
    }

    #[test]
    fn only_a_dropped_or_failed_report_moves_the_ticket() {
        assert_eq!(
            effects_of(ReportOutcome::Failed).ticket,
            Some(TicketState::Ended)
        );
        assert_eq!(
            effects_of(ReportOutcome::Dropped).ticket,
            Some(TicketState::Ended)
        );
        for outcome in [
            ReportOutcome::Waiting,
            ReportOutcome::Ready,
            ReportOutcome::Blocked,
        ] {
            assert_eq!(effects_of(outcome).ticket, None, "{outcome:?}");
        }
    }

    #[test]
    fn a_pending_assignment_that_never_started_is_relaunched() {
        assert_eq!(
            recovery_for(AssignmentState::Pending, false),
            Recovery::Relaunch
        );
    }

    #[test]
    fn a_running_assignment_whose_session_is_gone_is_replaced() {
        assert_eq!(
            recovery_for(AssignmentState::Running, false),
            Recovery::EndAndRelaunch
        );
    }

    #[test]
    fn a_live_or_finished_assignment_is_left_alone() {
        assert_eq!(
            recovery_for(AssignmentState::Pending, true),
            Recovery::Leave
        );
        assert_eq!(
            recovery_for(AssignmentState::Running, true),
            Recovery::Leave
        );
        assert_eq!(recovery_for(AssignmentState::Ended, false), Recovery::Leave);
    }

    #[test]
    fn a_ticket_gets_one_worktree_one_branch_and_one_session() {
        let plan = launch_plan(
            Path::new("/exec"),
            Path::new("/repo"),
            "0192f0c4-6a1f-7c31-9b0e-2d5b7c9a1e42",
            "main",
            "issue:7",
            "o/r",
            "mandate-9",
        );

        assert_eq!(
            (
                plan.session.as_str(),
                plan.branch.as_str(),
                plan.directory.as_path()
            ),
            (
                "worker-0192f0c4-6a1f-7c31-9b0e-2d5b7c9a1e42",
                "worker/0192f0c4-6a1f-7c31-9b0e-2d5b7c9a1e42",
                Path::new("/exec/0192f0c4-6a1f-7c31-9b0e-2d5b7c9a1e42"),
            )
        );
    }

    #[test]
    fn a_plan_carries_identifiers_and_nothing_that_could_be_a_credential() {
        let plan = launch_plan(
            Path::new("/exec"),
            Path::new("/repo"),
            "t-1",
            "main",
            "issue:7",
            "o/r",
            "mandate-9",
        );

        let mut variables: Vec<(&str, &str)> = plan
            .variables
            .iter()
            .map(|(variable, value)| (variable.name(), value.as_str()))
            .collect();
        variables.sort_unstable();
        assert_eq!(
            variables,
            vec![
                ("OPENBAD_BRANCH", "worker/t-1"),
                ("OPENBAD_MANDATE", "mandate-9"),
                ("OPENBAD_PROJECT", "/repo"),
                ("OPENBAD_REPOSITORY", "o/r"),
                ("OPENBAD_SOURCE", "main"),
                ("OPENBAD_THREAD", "issue:7"),
                ("OPENBAD_TICKET", "t-1"),
                ("OPENBAD_WORKTREE", "/exec/t-1"),
            ]
        );
        for (name, value) in variables {
            let haystack = format!("{name}={value}").to_ascii_uppercase();
            assert!(
                !["SECRET", "TOKEN", "PASSWORD", "API_KEY", "CREDENTIAL"]
                    .iter()
                    .any(|word| haystack.contains(word)),
                "{name} looks like a credential"
            );
        }
    }

    #[test]
    fn the_prompt_states_the_mandate_and_the_two_things_it_does_not_buy() {
        let plan = launch_plan(
            Path::new("/exec"),
            Path::new("/repo"),
            "t-1",
            "main",
            "issue:7",
            "o/r",
            "mandate-9",
        );
        let mandate = crate::mandate::Mandate::new(
            "policy-3",
            "issue:7",
            crate::mandate::MandateStrength::Explicit,
            vec!["label agent:run".to_owned()],
            "alice",
            "main",
            "label agent:run",
        );

        let prompt = ticket_prompt(&plan, &mandate);

        assert!(
            prompt.contains("/exec/t-1")
                && prompt.contains("worker/t-1")
                && prompt.contains("alice")
                && prompt.contains("policy-3")
                && prompt.contains("Never push to a protected branch")
                && prompt.contains("not asked to approve each step")
                && prompt.contains("Do not ask a person"),
            "the executor is told where it works and what its mandate does not buy:\n{prompt}"
        );
    }

    #[test]
    fn a_hostile_ticket_id_cannot_escape_the_execution_root() {
        let plan = launch_plan(
            Path::new("/exec"),
            Path::new("/repo"),
            "../../etc/passwd",
            "main",
            "issue:7",
            "o/r",
            "mandate-9",
        );

        assert_eq!(plan.directory, Path::new("/exec/------etc-passwd"));
        assert!(!plan.branch.contains(".."));
        assert!(!plan.session.contains('/'));
    }
}
