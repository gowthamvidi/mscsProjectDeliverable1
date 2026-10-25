"""Repeatable FIFO/LRU paging and real thread synchronization demonstrations."""
from collections import deque
from threading import Thread, Lock, Barrier

REFERENCE = (7, 0, 1, 2, 0, 3, 0, 4, 2, 3, 0, 3, 2)

def paging(policy, frames=3, references=REFERENCE, log=print):
    """Return hits/faults; FIFO order changes on load, LRU on every access."""
    policy = policy.lower()
    if policy not in ('fifo', 'lru') or frames < 1:
        raise ValueError('policy must be fifo or lru; frames must be positive')
    resident = []
    order = deque()
    faults = hits = 0
    log(f'{policy.upper()} PAGING | frames={frames} | references={list(references)}')
    log('STEP PAGE RESULT VICTIM FRAMES')
    for step, page in enumerate(references, 1):
        victim = '-'
        if page in resident:
            hits += 1
            result = 'HIT'
            if policy == 'lru':
                order.remove(page)
                order.append(page)
        else:
            faults += 1
            result = 'FAULT'
            if len(resident) == frames:
                victim = order.popleft()
                resident[resident.index(victim)] = page
            else:
                resident.append(page)
            order.append(page)
        log(f'{step:>4} {page:>4} {result:<6} {str(victim):>6} {resident}')
    log(f'TOTAL: faults={faults} hits={hits} references={faults+hits}')
    return {'faults': faults, 'hits': hits}

def sync_demo(iterations=100, log=print):
    """Force an unsafe lost update, then protect each read-modify-write."""
    if iterations < 1:
        raise ValueError('iterations must be positive')
    def run(protected):
        shared = {'counter': 0}
        lock = Lock()
        barrier = Barrier(2)
        def worker():
            for _ in range(iterations):
                if protected:
                    # All accesses participating in the update use this lock.
                    with lock:
                        shared['counter'] += 1
                else:
                    old = shared['counter']
                    barrier.wait()  # both threads read the same old value
                    shared['counter'] = old + 1
                    barrier.wait()  # both writes finish before the next read
        workers = [Thread(target=worker) for _ in range(2)]
        for thread in workers: thread.start()
        for thread in workers: thread.join()
        return shared['counter']
    unsafe, safe = run(False), run(True)
    expected = 2 * iterations
    log(f'SHARED COUNTER | threads=2 | increments/thread={iterations}')
    log(f'Expected counter: {expected}')
    log(f'UNPROTECTED (forced race): actual={unsafe}, lost={expected-unsafe}')
    log(f'PROTECTED (threading.Lock): actual={safe}, lost={expected-safe}')
    log('PASS: protected result equals expected' if safe == expected else 'FAIL')
    return {'expected': expected, 'unsafe': unsafe, 'protected': safe}
