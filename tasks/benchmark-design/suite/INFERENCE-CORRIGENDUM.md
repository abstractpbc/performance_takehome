# Candidate inference/identity clarification

This note clarifies the existing evaluator and its new descriptive diagnostics.
It does not change the accepted frozen plan, calibration, raw captures or old reports.
The resample count, minimum effects, existing statistical/sufficiency rules and
inconclusive precedence remain unchanged. New identity-consistency refusal checks
are described below. No full/quick comparison, measured MI355X power or finite-sample family-wise
guarantee is established by these diagnostics.

The comparison family jointly covers **matched cell/load groups × four metrics**:
TTFT p50/p90, strict fluidity mean and MDD p99. Full mode has12 measurement groups,
so the joint family is48;14 groups includes the two A/A groups. Within one A/A
comparison the family is4. Requests are independently resampled within each pooled
cell/load group; matching cell labels is not paired-request/block/cluster resampling.
Repetitions and shared queue state can violate the IID/exchangeability assumption.

Comparison reports now expose adjusted confidence, family size, resamples, nominal
tail probability, expected draws per tail and the percentile order index. At2000
draws/family48, each tail has only about1.04 expected draws. These are diagnostics,
not new vetoes or decision thresholds. For p99, reports also expose sample support
under an explicitly conditional IID-continuous illustration: at n=90, the probability
that the sample maximum is below the population p99 is0.99^90≈0.405. More bootstrap
draws do not add observations or establish population coverage or measured power.
Any proposed inference/resampling change requires a separately versioned design,
appropriate controls and real runtime/budget admission; none is automatically applied.

The historical `min_effect.ftgr_rel` field is applied to the **relative MDD-p99
difference**. Since FTGR is inverse MDD, MDD+10% means FTGR−9.09%, while MDD−10%
means FTGR+11.11%. This note labels the implemented policy; it does not retroactively
change that threshold to symmetric ±10% FTGR or reinterpret old decisions.

Warmup is four unscored requests at the **first load per cell/repetition**, as
`warmup.requests_per_cell` and the population checks specify. Full mode has42 units
and84 warmups, not168 under the old “four per unit” prose interpretation. Scheduling
and historical evidence remain unchanged.

Declared real calibration must now match the run and selected verified tokenizer's
model and stable asset/count/offset semantics. The new calibration/comparison
identity checks ignore additional provenance fields; the existing run-versus-selected
full tokenizer identity equality still applies during evaluation. The
model, asset hashes, tokenizer runtime, count/timing/prompt semantics, Unicode database
and offset mapping are bound. Real calibration missing those declared semantics
is refused explicitly. Unfrozen plans retain existing gates; explicit mock/fit-only
legacy cases remain supported. Real evaluation cannot reuse an explicit mock fit.

The comparison API independently checks plan consistency, recorded digests, model and
tokenizer semantics. The normal CLI already constructs one protected, verified
tokenizer for both runs; the earlier mock-only experiment did not demonstrate an
arbitrary-model bypass of that CLI. Image and lock-digest differences remain permitted
for legitimate baseline/candidate engine changes. Fixtures exercise identity predicates
without claiming that identity-only stand-ins establish real token counts or scores.
The CLI supplies the actual plan-file digest. Direct API callers must supply a
trusted `plan_digest_hex` for that file binding; equal recorded digests alone show
consistency, not authentication of an unverified plan dictionary.
