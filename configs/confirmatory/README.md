# Confirmatory configurations

No confirmatory LLM study has been frozen or executed. Create an explicit live
model configuration only after choosing a provider/model, rates, budget, rounds,
run count, and a reviewed statistical protocol. `freeze-config` writes a new
configuration with the protocol's exact SHA-256; it never overwrites a file.

A byte hash prevents accidental protocol drift. It does not itself establish
preregistration, adequate power, or independent review. Keep pilots and
confirmatory attempts in separate run classes and retain all failed attempts.
