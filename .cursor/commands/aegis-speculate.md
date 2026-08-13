# /aegis-speculate

Run the speculative decoding pipeline.

## Steps
1. Generate a draft token batch.
2. Verify against the target state.
3. Accept the longest valid prefix.
4. Fall back if confidence or correctness is insufficient.
5. Report acceptance ratio and latency.
