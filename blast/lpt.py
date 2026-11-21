def lpt(items, k):
    """items: list of (ms, number). Returns (makespan_ms, workers)."""
    import heapq
    ws = [[] for _ in range(k)]
    heap = [(0, i) for i in range(k)]
    for ms, n in sorted(items, reverse=True):
        load, i = heapq.heappop(heap)
        ws[i].append(n)
        heapq.heappush(heap, (load + ms, i))
    return max(l for l, _ in heap), ws


def best_split(items, max_k=8, worker_ms=2000):
    if not items:
        return 0, []
    best = None
    for k in range(1, min(max_k, len(items)) + 1):
        mk, ws = lpt(items, k)
        cost = mk + worker_ms * k
        if best is None or cost < best[0]:
            best = (cost, ws)
    return best
