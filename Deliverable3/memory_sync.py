"""Deliverable 3: educational frame-table paging and bounded-buffer threading."""
from collections import deque, defaultdict
from dataclasses import dataclass
import queue
import threading
import time

@dataclass
class Frame:
    pid: str
    page: int
    inserted: int
    accessed: int

class Paging:
    def __init__(self, frames=3, page_size=4096, algorithm='FIFO'):
        if frames < 1 or page_size < 1: raise ValueError('frames and page size must be positive')
        self.capacity, self.page_size, self.algorithm = frames, page_size, algorithm.upper()
        if self.algorithm not in ('FIFO','LRU'): raise ValueError('algorithm must be FIFO or LRU')
        self.table = [None] * frames
        self.tick = self.faults = self.hits = self.evictions = 0
        self.process_faults = defaultdict(int)
        self.allocated = defaultdict(set)

    def access(self, pid, page):
        pid = str(pid); page = int(page)
        if page < 0: raise ValueError('page must be nonnegative')
        self.tick += 1
        self.allocated[pid].add(page)
        for idx,f in enumerate(self.table):
            if f and (f.pid,f.page)==(pid,page):
                f.accessed = self.tick
                self.hits += 1
                return f'HIT     process={pid} page={page} frame={idx}'
        self.faults += 1; self.process_faults[pid] += 1
        index = next((i for i,f in enumerate(self.table) if f is None),None)
        victim = None
        if index is None:
            index = min(range(self.capacity), key=lambda i: self.table[i].inserted if self.algorithm=='FIFO' else self.table[i].accessed)
            victim = self.table[index]
            self.evictions += 1
        self.table[index] = Frame(pid,page,self.tick,self.tick)
        detail = f' EVICT process={victim.pid} page={victim.page}' if victim else ''
        return f'FAULT   process={pid} page={page} frame={index}{detail}'

    def deallocate(self,pid):
        pid=str(pid); released=0
        for i,f in enumerate(self.table):
            if f and f.pid==pid: self.table[i]=None; released+=1
        self.allocated.pop(pid,None)
        return f'DEALLOC process={pid} frames_released={released}'

    def status(self):
        lines=[f'Algorithm={self.algorithm} frames={self.capacity} page_size={self.page_size} bytes',
               f'faults={self.faults} hits={self.hits} evictions={self.evictions} fault_rate={self.faults/max(1,self.hits+self.faults):.2%}']
        for i,f in enumerate(self.table):lines.append(f'frame {i}: '+(f'process={f.pid} page={f.page}' if f else 'FREE'))
        for pid in sorted(self.allocated):
            resident=sum(f is not None and f.pid==pid for f in self.table)
            lines.append(f'process={pid}: virtual_pages={len(self.allocated[pid])} resident_pages={resident} resident_bytes={resident*self.page_size} faults={self.process_faults[pid]}')
        return '\n'.join(lines)

def bounded_buffer_demo(items=6, size=2, delay=.004):
    if items<1 or size<1: raise ValueError('items and capacity must be positive')
    buffer=deque(); lock=threading.Lock(); empty=threading.Semaphore(size); full=threading.Semaphore(0)
    events=[]; consumed=[]; produced=[]
    def producer():
        for value in range(1,items+1):
            empty.acquire()
            with lock:
                buffer.append(value); produced.append(value)
                events.append(f'PRODUCER put={value} buffer={list(buffer)} (mutex held)')
            full.release(); time.sleep(delay)
    def consumer():
        for _ in range(items):
            full.acquire()
            with lock:
                value=buffer.popleft(); consumed.append(value)
                events.append(f'CONSUMER got={value} buffer={list(buffer)} (mutex held)')
            empty.release(); time.sleep(delay/2)
    a=threading.Thread(target=producer); b=threading.Thread(target=consumer)
    a.start();b.start();a.join(timeout=10);b.join(timeout=10)
    if a.is_alive() or b.is_alive(): raise RuntimeError('deadlock detected')
    assert produced==consumed==list(range(1,items+1))
    assert not buffer
    events.append(f'PASS produced={len(produced)} consumed={len(consumed)} final_buffer=0 no_deadlock=True')
    return '\n'.join(events)

def comparison(sequence=(1,2,3,1,4,1,2,5,1,2,3,4),frames=3):
    rows=[]
    for algo in ('FIFO','LRU'):
        m=Paging(frames=frames,algorithm=algo)
        trace=[m.access('P1',i) for i in sequence]
        rows.append((algo,m.faults,m.hits,m.evictions,'\n'.join(trace),m.status()))
    return rows

if __name__=='__main__':
    for algo,faults,hits,evictions,trace,status in comparison():
        print('\n===',algo,'===\n'+trace+'\n'+status)
    print('\n=== PRODUCER CONSUMER ===\n'+bounded_buffer_demo())
