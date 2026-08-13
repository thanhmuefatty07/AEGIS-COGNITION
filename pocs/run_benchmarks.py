import time
import random

# ==========================================
# 1. SEMANTIC CACHE SIMULATOR / BENCHMARK
# ==========================================
class MockSemanticCache:
    def __init__(self, threshold=0.95):
        self.threshold = threshold
        self.records = []
    
    def cosine_similarity(self, v1, v2):
        if len(v1) != len(v2) or not v1:
            return 0.0
        dot_product = sum(x * y for x, y in zip(v1, v2))
        norm_a = sum(x * x for x in v1) ** 0.5
        norm_b = sum(y * y for y in v2) ** 0.5
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot_product / (norm_a * norm_b)

    pub_lookup = None
    def lookup(self, prompt, embedding, namespace):
        # 1. Exact match
        for r in self.records:
            if r['namespace'] == namespace and r['prompt'].strip() == prompt.strip():
                return r['response'], 1.0
        
        # 2. Semantic match
        best_score = -1.0
        best_response = None
        for r in self.records:
            if r['namespace'] == namespace:
                sim = self.cosine_similarity(r['embedding'], embedding)
                if sim > best_score and sim >= self.threshold:
                    best_score = sim
                    best_response = r['response']
        return best_response, best_score

    def insert(self, prompt, response, embedding, namespace):
        self.records = [r for r in self.records if not (r['namespace'] == namespace and r['prompt'] == prompt)]
        self.records.append({
            'prompt': prompt,
            'response': response,
            'embedding': embedding,
            'namespace': namespace
        })

def run_cache_benchmark():
    print("\n=== RUNNING SEMANTIC CACHE BENCHMARK ===")
    cache = MockSemanticCache(threshold=0.90)
    namespace = "cve-research-task"
    
    # Pre-populate cache
    cache.insert(
        "list the latest CVEs for Aegis",
        "CVE-2026-1001 (RCE), CVE-2026-1002 (OOB)",
        [0.95, 0.05, 0.0],
        namespace
    )
    
    # Test queries
    exact_query = "list the latest CVEs for Aegis"
    semantic_query = "show the recent CVE vulnerabilities in Aegis"
    unrelated_query = "what is the capital of France"
    
    exact_embedding = [0.95, 0.05, 0.0]
    semantic_embedding = [0.92, 0.08, 0.0]
    unrelated_embedding = [0.0, 0.1, 0.99]
    
    # 1. Exact Hit
    start = time.perf_counter()
    res, score = cache.lookup(exact_query, exact_embedding, namespace)
    exact_time_us = (time.perf_counter() - start) * 1_000_000
    print(f"Exact Query: '{exact_query}' -> Hit Score: {score:.2f} (Took {exact_time_us:.2f} microseconds)")
    assert res is not None
    
    # 2. Semantic Hit
    start = time.perf_counter()
    res, score = cache.lookup(semantic_query, semantic_embedding, namespace)
    semantic_time_us = (time.perf_counter() - start) * 1_000_000
    print(f"Semantic Query: '{semantic_query}' -> Hit Score: {score:.2f} (Took {semantic_time_us:.2f} microseconds)")
    assert res is not None
    
    # 3. Cache Miss (requires mock LLM roundtrip of 1.2 seconds)
    start = time.perf_counter()
    res, score = cache.lookup(unrelated_query, unrelated_embedding, namespace)
    if res is None:
        # Simulate LLM call
        time.sleep(0.01) # fast simulation
        cache.insert(unrelated_query, "Paris", unrelated_embedding, namespace)
    miss_time_ms = (time.perf_counter() - start) * 1000
    print(f"Miss Query: '{unrelated_query}' -> Miss! (Simulated fetch took {miss_time_ms:.2f} ms)")


# ==========================================
# 2. TOOL BATCHING BENCHMARK
# ==========================================
def simulate_single_vs_batched():
    print("\n=== RUNNING TOOL BATCHING COMPARATIVE ANALYSIS ===")
    
    # We want to perform 5 click interactions on elements: [12, 14, 16, 18, 20]
    element_indices = [12, 14, 16, 18, 20]
    
    # Sequential (Single roundtrips)
    # LLM -> click -> Observation -> LLM -> click ...
    print("Sequential Execution (Baseline):")
    total_latency_seq = 0.0
    llm_turns_seq = 0
    for idx in element_indices:
        # LLM Turn (takes simulated 800ms)
        total_latency_seq += 0.8
        llm_turns_seq += 1
        # Tool execution (takes 100ms)
        total_latency_seq += 0.1
    print(f"  - Total LLM Turns: {llm_turns_seq}")
    print(f"  - Total Latency: {total_latency_seq:.2f} seconds")
    
    # Batched (Multi-Act)
    # LLM -> Batch[click 12, click 14, ...] -> Concurrent execution -> Aggregated observation
    print("Batched Execution (Multi-Act):")
    total_latency_batch = 0.0
    llm_turns_batch = 0
    
    # Single LLM Turn to generate all actions (takes 800ms)
    total_latency_batch += 0.8
    llm_turns_batch += 1
    
    # Concurrent execution of the 5 tools (Max latency of 100ms in parallel)
    total_latency_batch += 0.1
    
    print(f"  - Total LLM Turns: {llm_turns_batch}")
    print(f"  - Total Latency: {total_latency_batch:.2f} seconds")
    reduction = (1 - (llm_turns_batch / llm_turns_seq)) * 100
    print(f"  --> LLM Call Reduction: {reduction:.1f}%")


# ==========================================
# 3. CODE-FIRST ORCHESTRATION (SAC) BENCHMARK
# ==========================================
def simulate_search_as_code():
    print("\n=== RUNNING SEARCH AS CODE (SaC) BENCHMARK ===")
    
    # Task: Search local index for CVEs, filter for "Aegis", extract details.
    # Traditional multi-turn agent:
    # 1. LLM -> search("CVE-2026") -> 10 items
    # 2. LLM -> filter("Aegis") -> 2 items
    # 3. LLM -> extract("div.vuln") -> final answer
    print("Multi-Turn Agent (Baseline):")
    llm_turns = 3
    tokens_in = 3000 + 4000 + 5000  # grows with history accum
    tokens_out = 150 + 150 + 150
    print(f"  - LLM Turns: {llm_turns}")
    print(f"  - Total Tokens: {tokens_in + tokens_out}")
    
    # Search-as-Code Agent:
    # 1. LLM -> writes program -> execution inside sandbox -> returns final answer in 1 turn
    print("Search-as-Code (SaC) Agent:")
    sac_turns = 1
    sac_tokens_in = 3000
    sac_tokens_out = 300 # generated program size
    print(f"  - LLM Turns: {sac_turns}")
    print(f"  - Total Tokens: {sac_tokens_in + sac_tokens_out}")
    
    reduction_turns = (1 - (sac_turns / llm_turns)) * 100
    reduction_tokens = (1 - ((sac_tokens_in + sac_tokens_out) / (tokens_in + tokens_out))) * 100
    print(f"  --> LLM Call Reduction: {reduction_turns:.1f}%")
    print(f"  --> Token Consumption Reduction: {reduction_tokens:.1f}%")


if __name__ == "__main__":
    run_cache_benchmark()
    simulate_single_vs_batched()
    simulate_search_as_code()
