export const meta = {
  name: 'ramco-gate-buildout',
  description: 'Build and adversarially verify the seven Ramco Payce eval gates (G1-G7) in parallel',
  phases: [{ title: 'Build' }, { title: 'Verify' }],
}

// Each gate is independent: its own box in the pipeline, its own golden data,
// its own pass mark. They fan out cleanly. Verification is a separate, hostile
// read of each gate, because a test that cannot fail proves nothing.

const ROOT = '/Users/code_rambo/Documents/FDE/Notes/Agent_core_3rdNov/ramco-payce-pilot'
const DATA = '/Users/code_rambo/Documents/FDE/Notes/Agent_core_3rdNov/ramco-payce-pilot/data/data-room'

const CONTEXT = `
Project root: ${ROOT}. Read ${ROOT}/.claude/skills/ramco-pilot/SKILL.md FIRST — it holds
the measured data facts, the model ids, the AWS scoping rule and the house rules.
Data room: ${DATA}.
Python: ${ROOT}/.venv/bin/python (3.12.11). AWS: source ../aws-env.sh before any aws call.
Write the implementation under src/pilot/ and the test under evals/.
Golden data goes in evals/golden/<gate>.jsonl, built by a script in scripts/, never by hand.
Rules in code, judgement in the model. Do not add a gate that is not listed.
`

const GATES = [
  { key: 'g1', box: 'PII gate', mark: '0 leaks',
    brief: 'Redact Aadhaar, PAN, Emirates ID, IBAN, IFSC, long account numbers, phone, email, and names before ANY later box. 104 tickets carry identifiers. The test asserts zero identifier survives into the text handed downstream, and that redaction is reversible only through a token map held outside the model path.' },
  { key: 'g2', box: 'Classify', mark: '>=95% accuracy',
    brief: 'Collapse 35 raw category labels into 10 canonical queues, build gold labels for all 1,367 tickets from the raw labels plus the resolution event, then classify with apac.amazon.nova-micro-v1:0 and score. Report per-queue accuracy, not just the mean, and name the worst queue.' },
  { key: 'g3', box: 'Dedupe', mark: 'one client = one case',
    brief: 'Find repeat incidents: same customer, same topic, inside one payroll window. Merge into one case. The test asserts a known repeat cluster collapses to a single case and that two genuinely different issues from one customer do NOT merge.' },
  { key: 'g4', box: 'Injection screen', mark: 'caught before the model',
    brief: 'Ticket PN-101129 carries a direct prompt injection. Catch it in code before any model call. Also handle instructions hidden in long attachments. The test asserts the screen fires AND that the system holds no tool that could honour the instruction even if the screen failed — prove the second with an absence-of-capability assertion, not a prompt.' },
  { key: 'g5', box: 'Answer (RAG)', mark: 'faithfulness >=0.90, right document',
    brief: 'Ragas on Bedrock (judge au.anthropic.claude-haiku-4-5-20251001-v1:0, embedder amazon.titan-embed-text-v2:0). Score faithfulness, answer_relevancy, context_precision, context_recall. The golden set MUST include the stale-KB case: a June payslip question whose correct answer uses the INR 15,000 PF ceiling, not the INR 25,000 one that took effect 2026-08-01. An answer that is faithful to the wrong-dated document still fails.' },
  { key: 'g6', box: 'Output check', mark: 'no false, biased or harmful draft',
    brief: 'Post-check in code, not in the prompt: every factual claim in the draft must cite a retrieved passage; no money figure may appear without a cited source; no draft may promise a timeline the SLA table does not support. The test runs over generated draft replies.' },
  { key: 'g7', box: 'Speed and cost', mark: 'first reply <=3 s p95, <=USD 0.02 a draft',
    brief: 'Replay the peak week from tickets_export.json. Measure p95 to first reply end to end and USD per draft from real Bedrock token counts. Beat the vendor baseline of 19 s p95. Report the latency ladder per box so the slowest box is named.' },
]

const FINDINGS = {
  type: 'object',
  properties: {
    gate: { type: 'string' },
    implementation: { type: 'string', description: 'files written and what each does' },
    passMark: { type: 'string' },
    measured: { type: 'string', description: 'the number the test actually produced' },
    passed: { type: 'boolean' },
    weaknesses: { type: 'array', items: { type: 'string' } },
  },
  required: ['gate', 'implementation', 'measured', 'passed'],
}

const VERDICT = {
  type: 'object',
  properties: {
    gate: { type: 'string' },
    testIsReal: { type: 'boolean', description: 'false if the test cannot fail, or passes trivially' },
    howItCouldPassWhileBroken: { type: 'string' },
    fixNeeded: { type: 'string' },
  },
  required: ['gate', 'testIsReal', 'howItCouldPassWhileBroken'],
}

const results = await pipeline(
  GATES,
  g => agent(
    `${CONTEXT}\nBuild gate ${g.key.toUpperCase()} — box "${g.box}", pass mark ${g.mark}.\n${g.brief}\n` +
    `Write src/pilot/<box>.py and evals/test_${g.key}_*.py, build the golden data with a script, run the test, and report the number you actually measured.`,
    { label: `build:${g.key}`, phase: 'Build', schema: FINDINGS }
  ),
  built => agent(
    `${CONTEXT}\nAdversarially review gate ${built.gate}. Here is what was built:\n` +
    `${built.implementation}\nIt reports: ${built.measured} (passed=${built.passed}).\n\n` +
    `Your job is to break the TEST, not the code. Read the test file. Can it pass while the ` +
    `pipeline is broken? Is the golden data circular — derived from the same logic under test? ` +
    `Does it assert on real output or on a mock? Mutate the implementation and confirm the test ` +
    `goes red; if it stays green, the test is not real. Report honestly.`,
    { label: `verify:${built.gate}`, phase: 'Verify', schema: VERDICT }
  )
)

const fake = results.filter(r => r && r.testIsReal === false)
return {
  gatesBuilt: GATES.length,
  verdicts: results,
  testsThatProveNothing: fake.map(f => `${f.gate}: ${f.howItCouldPassWhileBroken}`),
}
