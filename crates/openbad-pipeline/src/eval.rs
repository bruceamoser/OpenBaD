//! Admission: does a trigger start work, continue it, or go nowhere?
//!
//! Eval holds no state. Everything it needs arrives as arguments — the source's
//! policy, what is on the thread, and the triggers — so the same inputs always
//! produce the same decisions and every rule is testable without a store.
//!
//! Two boundaries are deliberate:
//!
//! * **Rules decide, not a model.** The gate is a first-match-wins rule table, so
//!   "why was this ignored?" has one answer rather than a sampled one.
//! * **It never infers a stop from text.** [`DecisionKind::Stop`] exists because
//!   only a person's stop withdraws a mandate, but it is produced by an explicit
//!   command, never by reading a comment. The prototype rejected the same
//!   shortcut: a classification like that needs structured facts.

use serde::{Deserialize, Serialize};

use crate::config::SourceConfig;
use crate::mandate::{Mandate, MandateStrength};
use crate::trigger::Trigger;

/// What an admission decision can be.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DecisionKind {
    /// Start work: a new ticket with a mandate.
    Accept,
    /// Deliver to an existing ticket as context. Grants no new authority.
    Attach,
    /// A person stopped this work.
    Stop,
    /// Nothing happens, for the stated reason.
    Ignore,
}

/// One decision, with enough context to be read on its own.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Decision {
    pub kind: DecisionKind,
    pub trigger_id: String,
    /// Always populated: a decision without a reason is a mystery later.
    pub reason: String,
    pub matched_rules: Vec<String>,
    /// Present only for [`DecisionKind::Accept`].
    pub mandate: Option<Mandate>,
}

impl Decision {
    fn ignore(trigger: &Trigger, rule: &str, reason: impl Into<String>) -> Self {
        Self {
            kind: DecisionKind::Ignore,
            trigger_id: trigger.id.clone(),
            reason: reason.into(),
            matched_rules: vec![rule.to_owned()],
            mandate: None,
        }
    }

    fn attach(trigger: &Trigger, rule: &str, reason: impl Into<String>) -> Self {
        Self {
            kind: DecisionKind::Attach,
            trigger_id: trigger.id.clone(),
            reason: reason.into(),
            matched_rules: vec![rule.to_owned()],
            mandate: None,
        }
    }

    fn accept(trigger: &Trigger, rule: &str, mandate: Mandate) -> Self {
        Self {
            kind: DecisionKind::Accept,
            trigger_id: trigger.id.clone(),
            reason: format!(
                "{} admitted under {} policy {}",
                mandate.requester,
                mandate.strength_label(),
                mandate.policy_version
            ),
            matched_rules: vec![rule.to_owned()],
            mandate: Some(mandate),
        }
    }

    /// What a person's stop produces. Called by the panel's stop command, never
    /// inferred from a message.
    #[must_use]
    pub fn stop(trigger_id: impl Into<String>, by: &str) -> Self {
        Self {
            kind: DecisionKind::Stop,
            trigger_id: trigger_id.into(),
            reason: format!("{by} stopped this work"),
            matched_rules: vec!["person-stop".to_owned()],
            mandate: None,
        }
    }
}

impl Mandate {
    fn strength_label(&self) -> &'static str {
        match self.strength {
            MandateStrength::Explicit => "an explicit request",
            MandateStrength::Inferred => "an inferred candidate",
        }
    }
}

/// What the pipeline already knows about the thread a trigger belongs to.
///
/// Passed in rather than looked up, so Eval stays free of storage and a caller
/// cannot accidentally evaluate against a half-loaded thread.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ThreadContext {
    /// A ticket is working on this thread now.
    pub has_live_ticket: bool,
    /// A ticket existed and ended without a person stopping it, so a person's
    /// message resumes it under the same mandate.
    pub has_ended_ticket: bool,
    /// A person stopped this thread's work. Nothing reaches it.
    pub stopped_by_person: bool,
    /// When the ticket on this thread last finished a turn.
    ///
    /// An executor that shares the person's identity cannot be told apart by
    /// name, so time is the evidence: a message written before the ticket's own
    /// last turn ended was either written by it or already delivered to it.
    pub own_spoke_at_ms: Option<i64>,
}

/// Everything one evaluation needs.
#[derive(Debug, Clone, Copy)]
pub struct EvalInput<'a> {
    pub source: &'a SourceConfig,
    pub policy_version: &'a str,
    pub thread: ThreadContext,
}

/// Judges a batch, preserving order.
///
/// A batch is judged together because the caller may route up to twenty decisions
/// in one step; the rules themselves are per trigger, so one bad trigger cannot
/// change the verdict on another.
#[must_use]
pub fn evaluate(input: &EvalInput<'_>, triggers: &[Trigger]) -> Vec<Decision> {
    triggers
        .iter()
        .map(|trigger| judge(input, trigger))
        .collect()
}

/// The rule table. First match wins, so the order below *is* the policy.
fn judge(input: &EvalInput<'_>, trigger: &Trigger) -> Decision {
    // 1. The agent's own activity never becomes work. The source drops it too;
    //    repeating the check here means a different source cannot reintroduce it.
    if input.source.is_own_bot(&trigger.actor) {
        return Decision::ignore(trigger, "own-bot", "the agent's own activity");
    }

    // 2. A stopped thread receives nothing, from anyone.
    if input.thread.stopped_by_person {
        return Decision::ignore(
            trigger,
            "stopped-by-person",
            "a person stopped this work; only a person can restart it",
        );
    }

    // 3. Admission requires the label the project named. A source with no labels
    //    admits nothing, which is the safe default.
    if trigger.kind.needs_labels() && !input.source.admits(&trigger.labels) {
        return Decision::ignore(
            trigger,
            "label-filter",
            format!(
                "the issue does not carry an admitting label ({})",
                if input.source.labels.is_empty() {
                    "none configured".to_owned()
                } else {
                    input.source.labels.join(", ")
                }
            ),
        );
    }

    // 4. A bot on the bot list may propose work, never request it.
    if input.source.is_bot_known(&trigger.actor) {
        if input.thread.has_live_ticket {
            return Decision::attach(
                trigger,
                "bot-context",
                "a known bot's message is context for the live ticket, not authority",
            );
        }
        let mandate = mandate_for(
            input,
            trigger,
            MandateStrength::Inferred,
            "bot-candidate",
            "proposed by a known bot",
        );
        return Decision::accept(trigger, "bot-candidate", mandate);
    }

    // 5. Only a person on the allowed list may originate or continue work.
    if !input.source.is_allowed(&trigger.actor) {
        return Decision::ignore(
            trigger,
            "not-allowed",
            format!(
                "{} is not on the allowed list ({}); recorded so the decision is visible",
                trigger.actor,
                if input.source.allowed.is_empty() {
                    "empty".to_owned()
                } else {
                    input.source.allowed.join(", ")
                }
            ),
        );
    }

    // 6. A labelled issue from an allowed person is an explicit request.
    if trigger.kind.needs_labels() {
        let matched = matching_label(input.source, &trigger.labels).unwrap_or_default();
        let mandate = mandate_for(
            input,
            trigger,
            MandateStrength::Explicit,
            "allowed-person",
            &format!("admitted by label {matched}"),
        );
        return Decision::accept(trigger, "allowed-person", mandate);
    }

    // 7. The pipeline's own output is not new input. An executor that comments on
    //    its own thread under the person's identity would otherwise resume itself
    //    forever; a message older than its last finished turn cannot be a reply
    //    to work it has not done yet.
    if let Some(spoke_at) = input.thread.own_spoke_at_ms {
        if trigger.at_ms <= spoke_at {
            return Decision::ignore(
                trigger,
                "own-output",
                "written before this thread's ticket last finished a turn",
            );
        }
    }

    // 8. Anything else on a thread with a live ticket is delivered to it.
    if input.thread.has_live_ticket {
        return Decision::attach(
            trigger,
            "live-ticket",
            "the live ticket owns messages on its own thread",
        );
    }

    // 9. A person's message on an ended, unstopped ticket resumes it under the
    //    same mandate — no new admission, which is what keeps the authority the
    //    work started with.
    if input.thread.has_ended_ticket {
        return Decision::attach(
            trigger,
            "resume",
            "resumes the ended ticket under its existing mandate",
        );
    }

    // 10. A message on a thread with no ticket and no admitting label is not work.
    Decision::ignore(
        trigger,
        "no-ticket",
        "no ticket on this thread and no admitting label",
    )
}

fn mandate_for(
    input: &EvalInput<'_>,
    trigger: &Trigger,
    strength: MandateStrength,
    rule: &str,
    received_via: &str,
) -> Mandate {
    Mandate::new(
        input.policy_version,
        trigger.thread.as_str(),
        strength,
        vec![rule.to_owned()],
        trigger.actor.clone(),
        &input.source.id,
        received_via,
    )
}

fn matching_label(source: &SourceConfig, labels: &[String]) -> Option<String> {
    labels
        .iter()
        .find(|label| {
            source
                .labels
                .iter()
                .any(|wanted| wanted.eq_ignore_ascii_case(label))
        })
        .cloned()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::trigger::{ThreadId, TriggerKind};

    fn source() -> SourceConfig {
        SourceConfig {
            id: "main".to_owned(),
            repository: "o/r".to_owned(),
            enabled: true,
            labels: vec!["agent:run".to_owned()],
            allowed: vec!["alice".to_owned()],
            bot_logins: vec!["openbad-bot".to_owned()],
            known_bots: vec!["helper-bot".to_owned()],
            poll_interval_secs: 120,
        }
    }

    fn issue(actor: &str, labels: &[&str]) -> Trigger {
        let mut trigger = Trigger::new(
            "main",
            TriggerKind::IssueAdmitted,
            ThreadId::issue("o/r", 7),
            "7",
        );
        trigger.actor = actor.to_owned();
        trigger.labels = labels.iter().map(|label| label.to_lowercase()).collect();
        trigger
    }

    fn comment(actor: &str) -> Trigger {
        let mut trigger = Trigger::new(
            "main",
            TriggerKind::IssueComment,
            ThreadId::issue("o/r", 7),
            "99",
        );
        trigger.actor = actor.to_owned();
        trigger
    }

    fn judge_one(thread: ThreadContext, trigger: &Trigger) -> Decision {
        let config = source();
        evaluate(
            &EvalInput {
                source: &config,
                policy_version: "v1",
                thread,
            },
            std::slice::from_ref(trigger),
        )
        .remove(0)
    }

    #[test]
    fn a_labelled_issue_from_an_allowed_person_is_an_explicit_request() {
        let decision = judge_one(ThreadContext::default(), &issue("alice", &["agent:run"]));
        assert_eq!(decision.kind, DecisionKind::Accept);
        let mandate = decision.mandate.expect("mandate");
        assert_eq!(mandate.strength, MandateStrength::Explicit);
        assert_eq!(mandate.policy_version, "v1");
        assert_eq!(mandate.scope, "o/r#7");
        assert_eq!(mandate.requester, "alice");
        assert!(mandate.received_via.contains("agent:run"));
        assert!(mandate.is_explicit());
    }

    #[test]
    fn an_unlabelled_issue_is_recorded_as_ignored_with_the_reason() {
        let decision = judge_one(ThreadContext::default(), &issue("alice", &["bug"]));
        assert_eq!(decision.kind, DecisionKind::Ignore);
        assert!(
            decision.reason.contains("admitting label"),
            "{}",
            decision.reason
        );
        assert!(decision.reason.contains("agent:run"), "names the policy");
        assert!(decision.mandate.is_none());
    }

    #[test]
    fn a_person_off_the_allowed_list_is_ignored_but_the_reason_is_visible() {
        let decision = judge_one(ThreadContext::default(), &issue("mallory", &["agent:run"]));
        assert_eq!(decision.kind, DecisionKind::Ignore);
        assert!(decision.reason.contains("not on the allowed list"));
        assert!(decision.reason.contains("mallory"), "names who asked");
        assert!(decision.reason.contains("alice"), "and who may");
    }

    #[test]
    fn a_known_bot_proposes_work_it_never_requests_it() {
        let mut trigger = issue("helper-bot", &["agent:run"]);
        trigger.actor = "helper-bot".to_owned();
        let decision = judge_one(ThreadContext::default(), &trigger);
        assert_eq!(decision.kind, DecisionKind::Accept);
        let mandate = decision.mandate.expect("mandate");
        assert_eq!(
            mandate.strength,
            MandateStrength::Inferred,
            "a bot cannot claim a person asked"
        );
        assert!(!mandate.is_explicit());
    }

    #[test]
    fn a_bot_that_is_not_on_the_bot_list_is_not_admitted() {
        let mut trigger = issue("random-bot", &["agent:run"]);
        trigger.actor = "random-bot".to_owned();
        let decision = judge_one(ThreadContext::default(), &trigger);
        assert_eq!(decision.kind, DecisionKind::Ignore);
        assert!(decision.reason.contains("not on the allowed list"));
    }

    #[test]
    fn the_agents_own_activity_never_becomes_work() {
        let mut trigger = issue("openbad-bot", &["agent:run"]);
        trigger.actor = "OpenBaD-Bot".to_owned();
        let decision = judge_one(ThreadContext::default(), &trigger);
        assert_eq!(decision.kind, DecisionKind::Ignore);
        assert_eq!(decision.matched_rules, ["own-bot"]);
    }

    #[test]
    fn context_on_a_live_ticket_is_delivered_not_admitted() {
        let thread = ThreadContext {
            has_live_ticket: true,
            ..ThreadContext::default()
        };
        let decision = judge_one(thread, &comment("alice"));
        assert_eq!(decision.kind, DecisionKind::Attach);
        assert!(decision.mandate.is_none(), "context grants no authority");
        assert_eq!(decision.matched_rules, ["live-ticket"]);
    }

    #[test]
    fn a_persons_message_resumes_an_ended_ticket_under_its_own_mandate() {
        let thread = ThreadContext {
            has_ended_ticket: true,
            ..ThreadContext::default()
        };
        let decision = judge_one(thread, &comment("alice"));
        assert_eq!(decision.kind, DecisionKind::Attach);
        assert_eq!(decision.matched_rules, ["resume"]);
        assert!(decision.mandate.is_none(), "resuming is not re-authorizing");
    }

    #[test]
    fn a_tickets_own_comment_does_not_resume_it() {
        let thread = ThreadContext {
            has_ended_ticket: true,
            own_spoke_at_ms: Some(1_000),
            ..ThreadContext::default()
        };

        // The executor signs in as the person, so authorship proves nothing and
        // only time does: this was written during the turn it would resume.
        let mut own = comment("alice");
        own.at_ms = 900;
        let decision = judge_one(thread, &own);
        assert_eq!(decision.kind, DecisionKind::Ignore);
        assert_eq!(decision.matched_rules, ["own-output"]);

        // A reply written after the turn ended is a person talking to the ticket.
        let mut reply = comment("alice");
        reply.at_ms = 1_001;
        let decision = judge_one(thread, &reply);
        assert_eq!(decision.kind, DecisionKind::Attach);
        assert_eq!(decision.matched_rules, ["resume"]);
    }

    #[test]
    fn a_message_during_a_live_turn_still_reaches_it() {
        let thread = ThreadContext {
            has_live_ticket: true,
            own_spoke_at_ms: Some(1_000),
            ..ThreadContext::default()
        };
        let mut during = comment("alice");
        during.at_ms = 1_001;
        let decision = judge_one(thread, &during);
        assert_eq!(
            decision.kind,
            DecisionKind::Attach,
            "a message newer than the last finished turn is not the pipeline's own"
        );
        assert_eq!(decision.matched_rules, ["live-ticket"]);
    }

    #[test]
    fn a_stopped_thread_receives_nothing_from_anyone() {
        let thread = ThreadContext {
            has_live_ticket: true,
            has_ended_ticket: true,
            stopped_by_person: true,
            ..ThreadContext::default()
        };
        for trigger in [issue("alice", &["agent:run"]), comment("alice")] {
            let decision = judge_one(thread, &trigger);
            assert_eq!(decision.kind, DecisionKind::Ignore);
            assert_eq!(decision.matched_rules, ["stopped-by-person"]);
        }
    }

    #[test]
    fn a_message_with_no_ticket_and_no_label_is_not_work() {
        let decision = judge_one(ThreadContext::default(), &comment("alice"));
        assert_eq!(decision.kind, DecisionKind::Ignore);
        assert_eq!(decision.matched_rules, ["no-ticket"]);
    }

    #[test]
    fn the_batch_preserves_order_and_counts_only_accepts() {
        let config = source();
        let decisions = evaluate(
            &EvalInput {
                source: &config,
                policy_version: "v1",
                thread: ThreadContext::default(),
            },
            &[
                issue("alice", &["agent:run"]),
                issue("alice", &["bug"]),
                issue("mallory", &["agent:run"]),
            ],
        );
        assert_eq!(decisions.len(), 3);
        assert_eq!(decisions[0].kind, DecisionKind::Accept);
        assert_eq!(decisions[1].kind, DecisionKind::Ignore);
        assert_eq!(decisions[2].kind, DecisionKind::Ignore);
        assert_eq!(
            decisions
                .iter()
                .filter(|decision| decision.kind == DecisionKind::Accept)
                .count(),
            1
        );
    }

    #[test]
    fn a_stop_comes_from_a_command_and_says_who() {
        let decision = Decision::stop("main:issue_admitted:o/r#7:7", "alice");
        assert_eq!(decision.kind, DecisionKind::Stop);
        assert!(decision.reason.contains("alice"));
    }
}
