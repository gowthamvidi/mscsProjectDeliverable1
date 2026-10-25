"""Deterministic, single-core simulated scheduling for myshell Deliverable 2.
Virtual seconds are mapped onto wall-clock delay using --scale; no actual OS CPU
scheduling or suspension of forked jobs is attempted.
"""
from dataclasses import dataclass
from collections import deque
import heapq
import time
import math

EPS = 1e-9
@dataclass
class Process:
    name: str
    burst: float
    priority: int = 5
    arrival: float = 0.0

class Scheduler:
    def __init__(self, scale=0.025, log=print):
        self.jobs=[]
        self.scale=scale
        self.log=log

    def add(self, name, burst, priority=5, arrival=0):
        if not name or any(p.name==name for p in self.jobs): raise ValueError('process name must be nonempty and unique')
        if not math.isfinite(burst) or not math.isfinite(arrival) or burst<=0 or arrival<0: raise ValueError('burst must be > 0; arrival must be >= 0')
        self.jobs.append(Process(name,float(burst),int(priority),float(arrival)))
        self.log(f'ADDED {name}: burst={burst:g}s priority={priority} arrival={arrival:g}s')

    def _execute(self, mode, quantum=None):
        if not self.jobs: raise ValueError('queue is empty; add processes first')
        if mode=='rr' and (quantum is None or not math.isfinite(quantum) or quantum<=0): raise ValueError('quantum must be positive')
        jobs=sorted(enumerate(self.jobs),key=lambda item:(item[1].arrival,item[0]))
        todo=[p for _,p in jobs]
        remaining={p.name:p.burst for p in todo}
        first={};complete={};timeline=[]
        ready=deque() if mode=='rr' else []
        i=0;t=0.;seq=0
        rank={p.name:n for n,p in enumerate(todo)}
        def enqueue():
            nonlocal i,seq
            while i<len(todo) and todo[i].arrival<=t+EPS:
                p=todo[i]; i+=1
                if mode=='rr':ready.append(p)
                else:heapq.heappush(ready,(-p.priority,rank[p.name],p));seq+=1
                self.log(f'[t={t:5.1f}] READY  {p.name} (priority {p.priority})')
        enqueue()
        self.log(f'=== {"ROUND ROBIN (quantum="+str(quantum)+"s)" if mode=="rr" else "PREEMPTIVE PRIORITY (larger number = higher priority)"} ===')
        while len(complete)<len(todo):
            if not ready:
                nxt=todo[i].arrival
                self.log(f'[t={t:5.1f}] IDLE until {nxt:.1f}')
                t=nxt;enqueue()
            p=ready.popleft() if mode=='rr' else heapq.heappop(ready)[2]
            if p.name not in first:first[p.name]=t
            start=t
            run=min(remaining[p.name],float(quantum)) if mode=='rr' else remaining[p.name]
            if mode=='priority' and i<len(todo):run=min(run,todo[i].arrival-t)
            if run<=EPS:
                enqueue()
                if mode=='rr':ready.append(p)
                else:heapq.heappush(ready,(-p.priority,rank[p.name],p));seq+=1
                continue
            self.log(f'[t={start:5.1f}] RUN    {p.name} for {run:g}s')
            if self.scale>0:time.sleep(run*self.scale)
            t+=run;remaining[p.name]=max(0.,remaining[p.name]-run)
            timeline.append((p.name,start,t))
            enqueue()  # newcomers arrive before RR process is re-enqueued
            if remaining[p.name]<=EPS:
                complete[p.name]=t
                self.log(f'[t={t:5.1f}] DONE   {p.name}')
            else:
                if mode=='rr':
                    ready.append(p)
                    self.log(f'[t={t:5.1f}] SWITCH {p.name} -> back of queue; remaining={remaining[p.name]:g}s')
                else:
                    heapq.heappush(ready,(-p.priority,rank[p.name],p));seq+=1
                    event = 'PREEMPT' if ready[0][2].name != p.name else 'CONTINUE'
                    self.log(f'[t={t:5.1f}] {event} {p.name}; remaining={remaining[p.name]:g}s')
        rows=[]
        self.log('\nPROCESS METRICS (simulated seconds)')
        self.log('NAME     ARRIVAL BURST PRIORITY WAIT TURNAROUND RESPONSE')
        for p in todo:
            turnaround=complete[p.name]-p.arrival
            wait=turnaround-p.burst
            response=first[p.name]-p.arrival
            rows.append(dict(name=p.name,waiting=wait,turnaround=turnaround,response=response))
            self.log(f'{p.name:<8} {p.arrival:>7.1f} {p.burst:>5.1f} {p.priority:>8} {wait:>5.1f} {turnaround:>10.1f} {response:>8.1f}')
        n=len(rows)
        avg={k:sum(r[k] for r in rows)/n for k in ('waiting','turnaround','response')}
        self.log(f'AVERAGE: waiting={avg["waiting"]:.2f}s turnaround={avg["turnaround"]:.2f}s response={avg["response"]:.2f}s')
        return {'rows':rows,'average':avg,'timeline':timeline,'finish':t}

    def round_robin(self, quantum):return self._execute('rr',float(quantum))
    def priority(self):return self._execute('priority')
