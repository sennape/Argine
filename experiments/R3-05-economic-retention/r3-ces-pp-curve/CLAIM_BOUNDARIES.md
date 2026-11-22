# Claim boundaries — R3-05 rung R3
- Offline, model-free replay of the frozen canonical stream (24,672 events, 514 passes) at 1.5 GiB. No model, no llama-server, no runtime C++ change.
- Miss and miss-byte counts are replay quantities. No runtime cost was measured. The proportional cost figures are PROPORTIONAL_COST_MODEL_ONLY and never override K3.
- K2/K3 are about the current PP mechanism and its invocation frequency only. Nothing here evaluates, supports or refutes NUE_X, NUE_LO or NUE_AMORT, and predictive retention overall is neither killed nor supported.
- K3 SURVIVES means only that sparse refresh does not by itself erase the replay effect; it is not a success. Benefits are tiny relative to the Belady headroom.
- Single stream, single budget, deterministic replay: no confidence intervals.
