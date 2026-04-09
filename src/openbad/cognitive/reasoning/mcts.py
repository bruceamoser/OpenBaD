"""Monte Carlo Tree Search (MCTS) reasoning strategy."""

from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openbad.cognitive.model_router import Priority
from openbad.cognitive.reasoning.base import ReasoningResult, ReasoningStep, ReasoningStrategy

if TYPE_CHECKING:
    from openbad.cognitive.model_router import ModelRouter


# ------------------------------------------------------------------ #
# Data types
# ------------------------------------------------------------------ #


@dataclass
class MCTSNode:
    """A node in the MCTS tree."""

    node_id: str
    state: str
    visits: int = 0
    value: float = 0.0
    children: list[MCTSNode] = field(default_factory=list)
    parent: MCTSNode | None = field(default=None, repr=False)
    action: str = ""
    depth: int = 0


@dataclass
class MCTSConfig:
    """Configuration for MCTS reasoning."""

    priority: Priority = Priority.HIGH
    max_iterations: int = 100
    exploration_constant: float = 1.414
    max_depth: int = 5
    expansion_count: int = 3


# ------------------------------------------------------------------ #
# Prompts
# ------------------------------------------------------------------ #

_EXPAND_PROMPT = (
    "You are exploring solutions to a problem.\n"
    "Given the current reasoning state, generate exactly {n} "
    "distinct next actions or thoughts.\n"
    "Number each:\n"
    "ACTION 1: <thought>\n"
    "ACTION 2: <thought>\n"
    "...\n\n"
    "Problem:\n{problem}\n\n"
    "Context:\n{context}\n\n"
    "Current state:\n{state}"
)

_ROLLOUT_PROMPT = (
    "You are quickly evaluating a reasoning path.\n"
    "Rate how promising this state is for solving the problem.\n"
    "Output exactly: VALUE: <number between 0.0 and 1.0>\n\n"
    "Problem:\n{problem}\n\n"
    "State:\n{state}"
)

_SYNTHESIZE_PROMPT = (
    "You are synthesizing a final answer.\n"
    "Given the problem and the best reasoning path found via search, "
    "output a clear final answer.\n\n"
    "Problem:\n{problem}\n\n"
    "Context:\n{context}\n\n"
    "Reasoning path:\n{path}\n\n"
    "FINAL ANSWER:"
)

_ACTION_PATTERN = re.compile(
    r"ACTION\s+\d+\s*:\s*(.+?)(?=ACTION\s+\d+|$)",
    re.DOTALL,
)
_VALUE_PATTERN = re.compile(r"VALUE\s*:\s*([\d.]+)")


# ------------------------------------------------------------------ #
# Strategy
# ------------------------------------------------------------------ #


class MCTSReasoning(ReasoningStrategy):
    """MCTS: selection → expansion → simulation → backpropagation."""

    def __init__(self, config: MCTSConfig | None = None) -> None:
        self._config = config or MCTSConfig()

    async def reason(
        self,
        prompt: str,
        context: str,
        model_router: ModelRouter,
    ) -> ReasoningResult:
        total_tokens = 0
        t0 = time.monotonic()

        adapter, model_id, decision = await model_router.route(
            self._config.priority,
        )

        root = MCTSNode(
            node_id=uuid.uuid4().hex[:8],
            state=f"Problem: {prompt}\nContext: {context}",
        )

        for _ in range(self._config.max_iterations):
            # 1. Selection
            leaf = self._select(root)

            # 2. Expansion (if not at max depth)
            if leaf.depth < self._config.max_depth:
                children, tokens = await self._expand(
                    prompt, context, leaf, adapter, model_id,
                )
                total_tokens += tokens
                if children:
                    leaf = children[0]  # pick first child for simulation

            # 3. Simulation (rollout)
            value, tokens = await self._simulate(
                prompt, leaf.state, adapter, model_id,
            )
            total_tokens += tokens

            # 4. Backpropagation
            self._backpropagate(leaf, value)

        # Extract best path (most visited children)
        best_path = self._best_path(root)

        # Synthesize
        path_text = "\n".join(
            f"Step {i + 1}: {n.state}" for i, n in enumerate(best_path)
        )
        synth_prompt = _SYNTHESIZE_PROMPT.format(
            problem=prompt, context=context, path=path_text,
        )
        result = await adapter.complete(synth_prompt, model=model_id)
        total_tokens += result.tokens_used
        final_answer = result.content.strip()

        latency = (time.monotonic() - t0) * 1000
        model_router.record_latency(decision.provider, latency)

        steps = tuple(
            ReasoningStep(
                step_number=i + 1,
                thought=node.state,
                conclusion=f"visits={node.visits}, value={node.value:.2f}",
            )
            for i, node in enumerate(best_path)
        )

        return ReasoningResult(
            final_answer=final_answer,
            steps=steps,
            total_tokens=total_tokens,
            total_latency_ms=latency,
            metadata={
                "model_id": model_id,
                "provider": decision.provider,
                "iterations": self._config.max_iterations,
                "total_nodes": self._count_nodes(root),
                "root_visits": root.visits,
            },
        )

    # ------------------------------------------------------------------ #
    # MCTS phases
    # ------------------------------------------------------------------ #

    def _select(self, node: MCTSNode) -> MCTSNode:
        """Select a leaf using UCB1."""
        current = node
        while current.children:
            current = self._ucb1_child(current)
        return current

    def _ucb1_child(self, node: MCTSNode) -> MCTSNode:
        """Pick the child with highest UCB1 score."""
        c = self._config.exploration_constant
        log_parent = math.log(node.visits) if node.visits > 0 else 0

        best_score = -1.0
        best_child = node.children[0]

        for child in node.children:
            if child.visits == 0:
                return child  # unexplored → pick immediately
            exploit = child.value / child.visits
            explore = c * math.sqrt(log_parent / child.visits)
            score = exploit + explore
            if score > best_score:
                best_score = score
                best_child = child

        return best_child

    async def _expand(
        self,
        problem: str,
        context: str,
        node: MCTSNode,
        adapter: object,
        model_id: str,
    ) -> tuple[list[MCTSNode], int]:
        """Generate child nodes via model."""
        expand_prompt = _EXPAND_PROMPT.format(
            n=self._config.expansion_count,
            problem=problem,
            context=context,
            state=node.state,
        )
        result = await adapter.complete(expand_prompt, model=model_id)  # type: ignore[union-attr]
        actions = _parse_actions(result.content, self._config.expansion_count)

        children: list[MCTSNode] = []
        for action in actions:
            child = MCTSNode(
                node_id=uuid.uuid4().hex[:8],
                state=action,
                parent=node,
                action=action,
                depth=node.depth + 1,
            )
            node.children.append(child)
            children.append(child)

        return children, result.tokens_used

    async def _simulate(
        self,
        problem: str,
        state: str,
        adapter: object,
        model_id: str,
    ) -> tuple[float, int]:
        """Rollout: quick evaluation of a state."""
        rollout_prompt = _ROLLOUT_PROMPT.format(
            problem=problem, state=state,
        )
        result = await adapter.complete(rollout_prompt, model=model_id)  # type: ignore[union-attr]
        value = _parse_value(result.content)
        return value, result.tokens_used

    @staticmethod
    def _backpropagate(node: MCTSNode, value: float) -> None:
        """Update visits and values from leaf to root."""
        current: MCTSNode | None = node
        while current is not None:
            current.visits += 1
            current.value += value
            current = current.parent

    # ------------------------------------------------------------------ #
    # Tree helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _best_path(root: MCTSNode) -> list[MCTSNode]:
        """Follow the most-visited children from root."""
        path: list[MCTSNode] = []
        current = root
        while current.children:
            best = max(current.children, key=lambda n: n.visits)
            path.append(best)
            current = best
        return path

    @staticmethod
    def _count_nodes(root: MCTSNode) -> int:
        """Count total nodes in the tree."""
        count = 0
        stack = [root]
        while stack:
            node = stack.pop()
            count += 1
            stack.extend(node.children)
        return count


# ------------------------------------------------------------------ #
# Parsing
# ------------------------------------------------------------------ #


def _parse_actions(text: str, expected: int) -> list[str]:
    """Extract actions from model response."""
    matches = _ACTION_PATTERN.findall(text)
    actions = [m.strip() for m in matches if m.strip()]
    if not actions:
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        actions = lines[:expected]
    return actions[:expected]


def _parse_value(text: str) -> float:
    """Extract numeric value from rollout response."""
    match = _VALUE_PATTERN.search(text)
    if match:
        return min(max(float(match.group(1)), 0.0), 1.0)
    return 0.5
