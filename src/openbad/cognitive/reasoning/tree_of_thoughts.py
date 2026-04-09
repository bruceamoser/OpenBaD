"""Tree-of-Thoughts (ToT) structured reasoning strategy."""

from __future__ import annotations

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
class ThoughtNode:
    """A single node in the thought tree."""

    node_id: str
    thought: str
    score: float = 0.0
    depth: int = 0
    parent_id: str | None = None
    children: list[ThoughtNode] = field(default_factory=list)


@dataclass
class ThoughtTree:
    """Full tree structure for inspection and tracing."""

    root_nodes: list[ThoughtNode] = field(default_factory=list)
    best_path: list[ThoughtNode] = field(default_factory=list)
    total_nodes_explored: int = 0
    total_nodes_pruned: int = 0


@dataclass
class TreeOfThoughtsConfig:
    """Configuration for ToT reasoning."""

    priority: Priority = Priority.HIGH
    branching_factor: int = 3
    max_depth: int = 3
    prune_threshold: float = 0.3


# ------------------------------------------------------------------ #
# Prompts
# ------------------------------------------------------------------ #

_GENERATE_PROMPT = (
    "You are exploring possible solution paths.\n"
    "Given the problem and current reasoning so far, "
    "generate exactly {n} distinct candidate next-thoughts.\n"
    "Number each candidate:\n"
    "CANDIDATE 1: <thought>\n"
    "CANDIDATE 2: <thought>\n"
    "...\n\n"
    "Problem:\n{problem}\n\n"
    "Context:\n{context}\n\n"
    "Reasoning so far:\n{path}"
)

_EVALUATE_PROMPT = (
    "You are evaluating a candidate thought in a reasoning chain.\n"
    "Rate how promising this thought is for solving the problem.\n"
    "Output exactly: SCORE: <number between 0.0 and 1.0>\n\n"
    "Problem:\n{problem}\n\n"
    "Thought to evaluate:\n{thought}"
)

_SYNTHESIZE_PROMPT = (
    "You are synthesizing a final answer from a reasoning path.\n"
    "Given the problem and the best chain of thoughts, "
    "produce a clear final answer.\n\n"
    "Problem:\n{problem}\n\n"
    "Context:\n{context}\n\n"
    "Reasoning path:\n{path}\n\n"
    "FINAL ANSWER:"
)

_CANDIDATE_PATTERN = re.compile(
    r"CANDIDATE\s+\d+\s*:\s*(.+?)(?=CANDIDATE\s+\d+|$)",
    re.DOTALL,
)

_SCORE_PATTERN = re.compile(r"SCORE\s*:\s*([\d.]+)")


# ------------------------------------------------------------------ #
# Strategy
# ------------------------------------------------------------------ #


class TreeOfThoughts(ReasoningStrategy):
    """Tree-of-Thoughts: explore multiple solution paths, prune, converge."""

    def __init__(self, config: TreeOfThoughtsConfig | None = None) -> None:
        self._config = config or TreeOfThoughtsConfig()

    async def reason(
        self,
        prompt: str,
        context: str,
        model_router: ModelRouter,
    ) -> ReasoningResult:
        tree = ThoughtTree()
        total_tokens = 0
        t0 = time.monotonic()

        adapter, model_id, decision = await model_router.route(
            self._config.priority,
        )

        # Generate root candidates
        root_candidates = await self._generate(
            prompt, context, "", adapter, model_id,
        )
        total_tokens += root_candidates[1]
        root_nodes: list[ThoughtNode] = []

        for thought_text in root_candidates[0]:
            node = ThoughtNode(
                node_id=uuid.uuid4().hex[:8],
                thought=thought_text,
                depth=0,
            )
            score, tokens = await self._evaluate(
                prompt, thought_text, adapter, model_id,
            )
            total_tokens += tokens
            node.score = score
            root_nodes.append(node)
            tree.total_nodes_explored += 1

        # Prune roots
        active = self._prune(root_nodes, tree)
        tree.root_nodes = root_nodes

        # Expand deeper levels
        for depth in range(1, self._config.max_depth):
            next_active: list[ThoughtNode] = []
            for parent in active:
                path_text = self._build_path([parent])
                children_texts, tokens = await self._generate(
                    prompt, context, path_text, adapter, model_id,
                )
                total_tokens += tokens

                for child_text in children_texts:
                    child = ThoughtNode(
                        node_id=uuid.uuid4().hex[:8],
                        thought=child_text,
                        depth=depth,
                        parent_id=parent.node_id,
                    )
                    score, tokens = await self._evaluate(
                        prompt, child_text, adapter, model_id,
                    )
                    total_tokens += tokens
                    child.score = score
                    parent.children.append(child)
                    tree.total_nodes_explored += 1

                surviving = self._prune(parent.children, tree)
                next_active.extend(surviving)

            active = next_active
            if not active:
                break

        # Find best path
        best_leaf = self._best_leaf(tree.root_nodes)
        best_path = self._trace_path(tree.root_nodes, best_leaf)
        tree.best_path = best_path

        # Synthesize final answer from best path
        path_text = self._build_path(best_path)
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
                thought=node.thought,
                conclusion=f"score={node.score:.2f}",
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
                "tree_nodes_explored": tree.total_nodes_explored,
                "tree_nodes_pruned": tree.total_nodes_pruned,
                "branching_factor": self._config.branching_factor,
                "max_depth": self._config.max_depth,
            },
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _generate(
        self, problem: str, context: str, path: str,
        adapter: object, model_id: str,
    ) -> tuple[list[str], int]:
        """Generate candidate thoughts. Returns (candidates, tokens_used)."""
        gen_prompt = _GENERATE_PROMPT.format(
            n=self._config.branching_factor,
            problem=problem,
            context=context,
            path=path or "(start)",
        )
        result = await adapter.complete(gen_prompt, model=model_id)  # type: ignore[union-attr]
        candidates = _parse_candidates(
            result.content, self._config.branching_factor,
        )
        return candidates, result.tokens_used

    async def _evaluate(
        self, problem: str, thought: str,
        adapter: object, model_id: str,
    ) -> tuple[float, int]:
        """Evaluate a single thought. Returns (score, tokens_used)."""
        eval_prompt = _EVALUATE_PROMPT.format(
            problem=problem, thought=thought,
        )
        result = await adapter.complete(eval_prompt, model=model_id)  # type: ignore[union-attr]
        score = _parse_score(result.content)
        return score, result.tokens_used

    def _prune(
        self, nodes: list[ThoughtNode], tree: ThoughtTree,
    ) -> list[ThoughtNode]:
        """Remove nodes below threshold. Returns surviving nodes."""
        surviving = [
            n for n in nodes if n.score >= self._config.prune_threshold
        ]
        tree.total_nodes_pruned += len(nodes) - len(surviving)
        return surviving

    def _best_leaf(self, roots: list[ThoughtNode]) -> ThoughtNode | None:
        """Find the highest-scoring leaf node in the tree."""
        best: ThoughtNode | None = None
        stack = list(roots)
        while stack:
            node = stack.pop()
            if not node.children and (best is None or node.score > best.score):
                best = node
            stack.extend(node.children)
        return best

    def _trace_path(
        self, roots: list[ThoughtNode], target: ThoughtNode | None,
    ) -> list[ThoughtNode]:
        """Trace from root to the target node."""
        if target is None:
            return []
        # Build parent lookup
        parent_map: dict[str, ThoughtNode] = {}
        stack = list(roots)
        while stack:
            node = stack.pop()
            for child in node.children:
                parent_map[child.node_id] = node
                stack.append(child)

        path = [target]
        current = target
        while current.node_id in parent_map:
            current = parent_map[current.node_id]
            path.append(current)
        path.reverse()
        return path

    @staticmethod
    def _build_path(nodes: list[ThoughtNode]) -> str:
        """Render a path as numbered text."""
        return "\n".join(
            f"Step {i + 1}: {n.thought}" for i, n in enumerate(nodes)
        )


# ------------------------------------------------------------------ #
# Parsing
# ------------------------------------------------------------------ #


def _parse_candidates(text: str, expected: int) -> list[str]:
    """Extract candidate thoughts from the model response."""
    matches = _CANDIDATE_PATTERN.findall(text)
    candidates = [m.strip() for m in matches if m.strip()]
    if not candidates:
        # Fallback: split by newlines and take first N non-empty lines
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        candidates = lines[:expected]
    return candidates[:expected]


def _parse_score(text: str) -> float:
    """Extract a numeric score from model response."""
    match = _SCORE_PATTERN.search(text)
    if match:
        return min(max(float(match.group(1)), 0.0), 1.0)
    return 0.5  # default if unparseable
