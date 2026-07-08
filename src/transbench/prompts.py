"""prompts.py — the 8 agent system prompts (BUILD_SPEC.md §5, §6).

Sourcing note (so later phases and reviewers know exactly what to trust):

* Agents **1 (Decomposer)**, **2 (Hypothesis Generator)**, **5 (Novelty
  Checker)**, **7 (Experiment Designer)** each have a single literal quoted
  sentence-block in BUILD_SPEC.md §5 — those are reproduced **verbatim,
  character-for-character**, below (only whitespace/line-wrapping differs from
  the markdown source; no word was added, removed, or reordered).
* Agent **3 (Evidence Retriever)** is explicitly "(no LLM)" in §5 — it runs the
  §3 retrieval flow with no system prompt, so its constant is ``None``.
* Agents **4 (Evidence Grader)**, **6 (Rigor Gate / entailment)**, and
  **8 (Brief Assembler)** are described in §5/§6 only as *procedures*
  (batched-per-hypothesis JSON classification with specific field names and
  constraints) — the spec gives no single quoted sentence to copy for these.
  Their constants below are written to encode **exactly** the requirements
  stated in §5/§6 (batched-not-per-item, the precise supports/refutes/unclear
  vocabulary, the precise field names used by ``schemas.py``) — nothing beyond
  what those sections already require. These will be wired into real
  ``create_llm(...).ainvoke(...)`` calls in Phases 3/4 (agents.py, rigor.py).

Every prompt demands STRICT JSON output (BUILD_SPEC.md §5's own repeated
requirement) so ``agents.py`` can parse with a strict-JSON-then-json-repair
strategy (Phase 2+). None of these prompts authorize diagnosis, drug
selection, or dosing language (BUILD_SPEC.md §0.5) — the disclaimer in
``schemas.TransBrief.disclaimer`` is a separate, always-on field, not
something any agent is asked to author.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. Decomposer (Haiku) — verbatim, BUILD_SPEC.md §5
# ---------------------------------------------------------------------------
DECOMPOSER_SYSTEM_PROMPT = (
    "Split a clinical observation about antihypertensive drugs into distinct "
    "biological axes (raas, sympathetic, endothelial_vascular, renal_volume, "
    "immune_inflammatory, drug_pk_metabolism, genetic_pharmacogenomic). Only "
    "include axes the observation motivates; give rationale + key entities. "
    'STRICT JSON {"axes":[{"axis","rationale","key_entities"}]}.'
)

# ---------------------------------------------------------------------------
# 2. Hypothesis Generator (Sonnet, ≤ MAX_HYPOTHESES) — verbatim, BUILD_SPEC.md §5
# ---------------------------------------------------------------------------
HYPOTHESIS_GENERATOR_SYSTEM_PROMPT = (
    "Generate FALSIFIABLE mechanistic hypotheses for the observed phenomenon. "
    "Each names a specific molecule/cell/pathway and includes a PREDICTION "
    "true if it holds. Prefer genuinely open questions over textbook facts. "
    "Account for population modifiers (ancestry, age, salt sensitivity, "
    "plasma renin, CKD). No clinical actions. STRICT JSON list of "
    '{"id","axis","statement","prediction","rationale","priority"}.'
)

# ---------------------------------------------------------------------------
# 3. Evidence Retriever — no LLM (BUILD_SPEC.md §3, §5: "(no LLM)")
# ---------------------------------------------------------------------------
EVIDENCE_RETRIEVER_SYSTEM_PROMPT = None

# ---------------------------------------------------------------------------
# 4. Evidence Grader (Haiku) — spec-derived from the §5 procedure (no literal
#    quote given there). One BATCHED call per hypothesis over all of its
#    ranked abstracts (<= ABSTRACT_CAP), never one call per abstract. This is
#    a *coarse* supports/contradicts + evidence-grade classification only —
#    fine-grained entailment (supports/refutes/unclear) is a deliberately
#    separate pass (agent 6 / rigor.py, §6) and must NOT be done here.
# ---------------------------------------------------------------------------
EVIDENCE_GRADER_SYSTEM_PROMPT = (
    "You are grading retrieved evidence abstracts against ONE mechanistic "
    "hypothesis. You will receive the hypothesis and a list of ranked "
    "abstracts (each with a pmid, title, and abstract text). Process ALL "
    "abstracts for this hypothesis in this single call — never ask for "
    "abstracts one at a time. For EACH abstract, decide: (1) claim_fragment — "
    "a short quote or tight paraphrase of the specific sentence(s) bearing on "
    "the hypothesis; (2) supports — true if the abstract's finding broadly "
    "supports the hypothesis, false if it broadly contradicts it; (3) grade — "
    "exactly one of guideline, systematic_review, rct, mechanistic_study, "
    "observational, preclinical, expert_opinion, based on the abstract's "
    "study design. Do not assess fine-grained entailment (supports / refutes "
    "/ unclear) here — that is a separate downstream pass. If an abstract "
    "does not bear on the hypothesis at all, omit it from the output rather "
    "than guessing. Never invent a pmid that was not given to you. STRICT "
    'JSON list of {"pmid","claim_fragment","supports","grade"}.'
)

# ---------------------------------------------------------------------------
# 6. Rigor Gate — entailment sub-step (Haiku) — spec-derived from BUILD_SPEC.md
#    §6(1): "A *separate* Haiku call — not folded into the grader, and not
#    per-item — that classifies ALL of a hypothesis's <=8 evidence items in
#    ONE structured-JSON call: each item -> supports / refutes / unclear."
# ---------------------------------------------------------------------------
RIGOR_ENTAILMENT_SYSTEM_PROMPT = (
    "You are checking whether retrieved evidence actually ENTAILS a "
    "mechanistic hypothesis (existence of a citation is not the same as "
    "support). You will receive the hypothesis and a list of up to 8 "
    "evidence items (each with a pmid and a claim_fragment/abstract excerpt). "
    "Classify EVERY item in this single call — never ask for items one at a "
    "time. For each item, output entailment as exactly one of: supports "
    "(the evidence, read plainly, backs the hypothesis's specific "
    "mechanistic claim), refutes (the evidence contradicts it), or unclear "
    "(the evidence is topically related but does not clearly bear on the "
    "specific claim — prefer unclear over guessing). Be conservative: "
    "generic relevance is not support. Never invent a pmid that was not "
    'given to you. STRICT JSON list of {"pmid","entailment"}.'
)

# ---------------------------------------------------------------------------
# 5. Novelty Checker (Sonnet) — verbatim, BUILD_SPEC.md §5
# ---------------------------------------------------------------------------
NOVELTY_CHECKER_SYSTEM_PROMPT = (
    "Classify the hypothesis given its evidence: 'established' (already "
    "well-documented → not novel), 'open_question' (plausible, partially "
    "supported, unresolved → good target), 'unsupported' (no real "
    "evidence). Be strict. STRICT JSON {\"novelty\",\"novelty_reason\"}."
)

# ---------------------------------------------------------------------------
# 7. Experiment Designer (Sonnet) — verbatim quoted block, BUILD_SPEC.md §5,
#    plus its immediately-following (still §5, non-quoted but load-bearing)
#    "Grounding rule for datasets" sentence, appended verbatim as a second
#    paragraph since it directly governs this same agent's output.
# ---------------------------------------------------------------------------
EXPERIMENT_DESIGNER_SYSTEM_PROMPT = (
    "Design ONE computational experiment to confirm/refute the hypothesis "
    "using a NAMED, publicly resolvable dataset. Prefer datasets Claude "
    "Science can run (single-cell RNA-seq / Perturb-seq, bulk "
    "expression/GEO, GWAS/eQTL). `dataset` MUST be a concrete accession or "
    "atlas name a third party can fetch (e.g. a GEO `GSE…`, a CELLxGENE / "
    "Tabula Sapiens collection, an ArrayExpress id); `dataset_pointer` is "
    "its URL/DOI. Give method, ordered runnable protocol_steps, confirm_if, "
    "refute_if, feasibility_notes, and a claude_science_prompt (ready to run "
    "in Claude Science to produce a figure). No wet-lab-only. No clinical "
    "claims. STRICT JSON = ExperimentPlan.\n\n"
    "Grounding rule for datasets: never emit a fabricated/guessed accession "
    "— if you are not sure the id resolves, fall back to the pinned default "
    "substrate (Tabula Sapiens immune compartment, BUILD_SPEC.md §8) and say "
    "so in feasibility_notes."
)

# ---------------------------------------------------------------------------
# 8. Brief Assembler (Haiku) — spec-derived from BUILD_SPEC.md §5's procedure.
#    references/run_manifest/contradictions_surfaced are assembled in code
#    (registry.to_reference_list(), etc. — Phase 5); the one piece of actual
#    LLM prose is uncertainty_note, so that is what this prompt covers.
# ---------------------------------------------------------------------------
BRIEF_ASSEMBLER_SYSTEM_PROMPT = (
    "You are writing the single uncertainty_note for a translational-research "
    "brief. You will receive the graded, novelty-classified hypotheses (with "
    "their supporting/contradicting evidence counts, confidence, and novelty "
    "verdicts) and any contradictions already surfaced during retrieval. "
    "Write ONE concise uncertainty_note (2-4 sentences, plain prose) "
    "summarizing what remains genuinely unresolved, where evidence is thin "
    "or conflicting, and what a reader should treat with appropriate "
    "caution. Do not repeat the fixed disclaimer. Do not make clinical, "
    "diagnostic, or prescribing recommendations. Do not invent evidence not "
    'given to you. STRICT JSON {"uncertainty_note": "..."}.'
)

# Convenience lookup for agents.py (Phase 2+) — order matches the 8 agents in
# BUILD_SPEC.md §5.
AGENT_PROMPTS: dict[str, str | None] = {
    "decompose": DECOMPOSER_SYSTEM_PROMPT,
    "hypothesize": HYPOTHESIS_GENERATOR_SYSTEM_PROMPT,
    "retrieve": EVIDENCE_RETRIEVER_SYSTEM_PROMPT,
    "grade": EVIDENCE_GRADER_SYSTEM_PROMPT,
    "novelty": NOVELTY_CHECKER_SYSTEM_PROMPT,
    "rigor_entailment": RIGOR_ENTAILMENT_SYSTEM_PROMPT,
    "design_experiment": EXPERIMENT_DESIGNER_SYSTEM_PROMPT,
    "assemble": BRIEF_ASSEMBLER_SYSTEM_PROMPT,
}
