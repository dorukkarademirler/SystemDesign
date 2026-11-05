"""
System design concepts, each as a small runnable demo.
python3 system_design_basics.py
"""

import hashlib
import queue
import random
import sqlite3
import threading
import time
from collections import OrderedDict, defaultdict


def heading(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


# ---------------------------------------------------------------
# 1. LOAD BALANCER (round robin vs least connections)
# Idea: spread requests over several identical servers.
# If a request of length 9 comes, it is distributed to servers as A, B, C, A, B, ...
# ---------------------------------------------------------------
class RoundRobinBalancer:
    def __init__(self, servers):
        self.servers = servers
        self.i = 0

    def pick(self):
        server = self.servers[self.i % len(self.servers)]
        self.i += 1
        return server


class LeastConnectionsBalancer:
    def __init__(self, servers):
        self.active = {s: 0 for s in servers}

    def pick(self):
        server = min(self.active, key=self.active.get)
        self.active[server] += 1
        return server

    def done(self, server):
        self.active[server] -= 1


def demo_load_balancer():
    heading("1. Load balancer")
    servers = ["server-A", "server-B", "server-C"]

    rr = RoundRobinBalancer(servers)
    counts = defaultdict(int)
    for _ in range(9):
        counts[rr.pick()] += 1
    print("Round robin, 9 requests ->", dict(counts))

    lc = LeastConnectionsBalancer(servers)
    slow = lc.pick()               # This is just for symbolizing a slow request

    routed = []
    for _ in range(6):
        s = lc.pick()              # a fast request arrives
        routed.append(s)
        lc.done(s)                 # ...and finishes immediately
    print("While A is stuck, fast requests went to:", routed)
    lc.done(slow)
    print("Takeaway: round robin is simple; least-connections adapts to slow requests.")


# ---------------------------------------------------------------
# 2. DATABASE INDEX (and why "SELECT everything" does not scale)
# an index turns a full scan into a fast lookup.
# ---------------------------------------------------------------
def demo_index():
    heading("2. Database index")
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE students (id INTEGER PRIMARY KEY, email TEXT, course TEXT)")
    rows = [(i, f"user{i}@school.ca", f"CS{random.randint(100, 499)}")
            for i in range(300_000)]
    db.executemany("INSERT INTO students VALUES (?, ?, ?)", rows)
    db.commit()

    target = "user299999@school.ca"

    def timed_lookup():
        t = time.perf_counter()
        for _ in range(20): # 20 is chosen to avg over 20 lookups
            db.execute("SELECT id FROM students WHERE email = ?", (target,)).fetchone()
        return (time.perf_counter() - t) / 20 * 1000
    # An index trades write speed and storage for faster reads, 
    # so I add them for columns we search by often and check the query plan to confirm they’re used
    no_index = timed_lookup()
    db.execute("CREATE INDEX idx_email ON students(email)")
    with_index = timed_lookup()

    print(f"Lookup without index: {no_index:8.3f} ms  (scans all 300,000 rows)")
    print(f"Lookup with index:    {with_index:8.3f} ms  (jumps straight to the row)")
    plan = db.execute("EXPLAIN QUERY PLAN SELECT id FROM students WHERE email = ?",
                      (target,)).fetchall()
    print("Query plan now:", plan[0][-1])
    print("Takeaway: index the columns you search by. Cost: extra storage and slower writes.")


# ---------------------------------------------------------------
# 3. PAGINATION 
# Idea: never fetch everything. Offset paging is simple; keyset paging scales.
# ---------------------------------------------------------------
def demo_pagination():
    heading("3. Pagination: fetch everything vs one page")
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE posts (id INTEGER PRIMARY KEY, body TEXT)")
    db.executemany("INSERT INTO posts VALUES (?, ?)",
                   [(i, "x" * 200) for i in range(200_000)])
    db.commit()

    t = time.perf_counter()
    everything = db.execute("SELECT id, body FROM posts").fetchall()
    t_all = (time.perf_counter() - t) * 1000

    t = time.perf_counter()
    page = db.execute("SELECT id FROM posts ORDER BY id LIMIT 20").fetchall()
    t_page = (time.perf_counter() - t) * 1000

    print(f"Fetch all rows + body: {t_all:8.2f} ms, {len(everything):,} rows in memory")
    print(f"Fetch one page of ids: {t_page:8.2f} ms, {len(page)} rows in memory")

    # OFFSET gets slower the deeper you go; keyset ("after this id") does not.
    t = time.perf_counter()
    db.execute("SELECT id FROM posts ORDER BY id LIMIT 20 OFFSET 190000").fetchall()
    t_offset = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    db.execute("SELECT id FROM posts WHERE id > 190000 ORDER BY id LIMIT 20").fetchall()
    t_keyset = (time.perf_counter() - t) * 1000
    print(f"Deep page with OFFSET: {t_offset:8.3f} ms")
    print(f"Deep page with keyset: {t_keyset:8.3f} ms")
    print("Takeaway: limit rows, select only needed columns, prefer keyset paging.")


# ---------------------------------------------------------------
# 4. CACHE (LRU)
# Idea: keep hot data in fast memory; evict the least recently used.
# ---------------------------------------------------------------
class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.data = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key, loader): # This is LRU, so the popular item might be forgotten after 49 hits on other keys
        if key in self.data:
            self.data.move_to_end(key)
            self.hits += 1
            return self.data[key]
        self.misses += 1
        value = loader(key)                 # slow path: hit the database
        self.data[key] = value
        if len(self.data) > self.capacity:
            self.data.popitem(last=False)   # evict least recently used
        return value


def demo_cache():
    heading("4. Cache (LRU)")

    def slow_database_read(course_id):
        time.sleep(0.002)                   # pretend each read costs 2 ms
        return f"course page {course_id}"

    # a few popular courses get most views.
    popular = list(range(10))
    traffic = [random.choice(popular) if random.random() < 0.8
               else random.randint(10, 999) for _ in range(1000)]

    t = time.perf_counter()
    for cid in traffic:
        slow_database_read(cid)
    no_cache = time.perf_counter() - t

    cache = LRUCache(capacity=50)
    t = time.perf_counter()
    for cid in traffic:
        cache.get(cid, slow_database_read)
    with_cache = time.perf_counter() - t

    rate = cache.hits / (cache.hits + cache.misses) * 100
    print(f"1000 reads, no cache:   {no_cache:6.2f} s")
    print(f"1000 reads, with cache: {with_cache:6.2f} s  (hit rate {rate:.0f}%)")
    print("Takeaway: caches help read-heavy, repetitive traffic. Cost: stale data and invalidation.")


# ---------------------------------------------------------------
# 5. QUEUE + BACKGROUND WORKER
# Idea: do slow work (email, reports) off the request path.
# ---------------------------------------------------------------
def demo_queue():
    heading("5. Queue and background worker")
    jobs = queue.Queue()
    done = []

    def worker():
        while True:
            job = jobs.get()
            if job is None:
                return
            time.sleep(0.05)               # slow work, e.g. sending an email
            done.append(job)

    def handle_request_inline(n):
        time.sleep(0.05)                   # user waits for the email to send

    t = time.perf_counter()
    for i in range(10):
        handle_request_inline(i)
    inline = time.perf_counter() - t

    thread = threading.Thread(target=worker)
    thread.start()
    t = time.perf_counter()
    for i in range(10):
        jobs.put(f"email-{i}")             # the user's request returns immediately
    responded = time.perf_counter() - t
    jobs.put(None)
    thread.join()

    print(f"10 requests, send email inline:  user waits {inline:5.2f} s")
    print(f"10 requests, queue the email:    user waits {responded:5.4f} s "
          f"({len(done)} emails still got sent)")
    print("Takeaway: queues keep responses fast and absorb traffic spikes. "
          "Cost: work happens later, so you must handle retries and failures.")


# ---------------------------------------------------------------
# 6. SHARDING (hash vs consistent hashing)
# Idea: split data across machines. Naive hash%N reshuffles almost everything
# when you add a server; consistent hashing moves far less.
# ---------------------------------------------------------------
def stable_hash(text):
    return int(hashlib.md5(text.encode()).hexdigest(), 16)


class ConsistentHashRing:
    def __init__(self, nodes, replicas=100):
        self.ring = sorted((stable_hash(f"{n}#{r}"), n)
                           for n in nodes for r in range(replicas))
        self.keys = [h for h, _ in self.ring]

    def node_for(self, key):
        import bisect
        i = bisect.bisect(self.keys, stable_hash(key)) % len(self.ring)
        return self.ring[i][1]


def demo_sharding():
    heading("6. Sharding: modulo vs consistent hashing")
    keys = [f"user-{i}" for i in range(10_000)]

    before = {k: stable_hash(k) % 4 for k in keys}
    after = {k: stable_hash(k) % 5 for k in keys}
    moved_mod = sum(before[k] != after[k] for k in keys) / len(keys) * 100

    ring4 = ConsistentHashRing([f"db{i}" for i in range(4)])
    ring5 = ConsistentHashRing([f"db{i}" for i in range(5)])
    moved_ring = sum(ring4.node_for(k) != ring5.node_for(k) for k in keys) / len(keys) * 100

    print(f"Add a 5th database, modulo hashing:     {moved_mod:5.1f}% of keys move")
    print(f"Add a 5th database, consistent hashing: {moved_ring:5.1f}% of keys move")
    print("Takeaway: sharding scales writes and storage, but pick the shard key "
          "carefully and expect cross-shard queries to be hard.")


# ---------------------------------------------------------------
# 7. RATE LIMITER (token bucket)
# protect the system from one noisy client.
# ---------------------------------------------------------------
class TokenBucket:
    def __init__(self, rate_per_sec, burst):
        self.rate = rate_per_sec
        self.capacity = burst
        self.tokens = burst
        self.last = time.monotonic()

    def allow(self):
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


def demo_rate_limiter():
    heading("7. Rate limiter (token bucket)")
    bucket = TokenBucket(rate_per_sec=5, burst=10)
    allowed = sum(bucket.allow() for _ in range(30))   # 30 requests in a burst
    print(f"30 instant requests: {allowed} allowed (burst size), {30 - allowed} rejected")
    time.sleep(1)
    allowed_later = sum(bucket.allow() for _ in range(30))
    print(f"After 1 second, 30 more: {allowed_later} allowed (about 5 tokens refilled)")
    print("Takeaway: allows short bursts, enforces an average rate. "
          "Return HTTP 429 to rejected clients.")


# ---------------------------------------------------------------
# 8. BACK-OF-ENVELOPE ESTIMATE
# Idea: rough numbers tell you how heavy the design must be.
# ---------------------------------------------------------------
def demo_estimation():
    heading("8. Back-of-envelope estimate")
    daily_users = 100_000
    views_per_user = 20
    peak_factor = 3                          # peak traffic vs average
    seconds_per_day = 86_400

    avg_rps = daily_users * views_per_user / seconds_per_day
    peak_rps = avg_rps * peak_factor
    print(f"{daily_users:,} users x {views_per_user} views/day")
    print(f"  average: {avg_rps:6.1f} requests/sec")
    print(f"  peak:    {peak_rps:6.1f} requests/sec")
    print("One modest server handles a few hundred simple req/s, so this needs a "
          "few servers for safety, not a huge cluster.")
    storage_gb = daily_users * 5 * 365 / 1_000_000   # 5 KB of new data per user per day
    print(f"Storage if each user adds 5 KB/day: about {storage_gb:.0f} GB per year.")
    print("Takeaway: do this arithmetic out loud in an interview before drawing boxes.")


if __name__ == "__main__":
    random.seed(7)
    demo_load_balancer()
    demo_index()
    demo_pagination()
    demo_cache()
    demo_queue()
    demo_sharding()
    demo_rate_limiter()
    demo_estimation()