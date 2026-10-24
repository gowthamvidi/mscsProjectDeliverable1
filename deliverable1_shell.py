#!/usr/bin/env python3
"""Deliverable 1: basic POSIX shell, Python 3.10+, no dependencies.

Run: python3 deliverable1_shell.py (Linux, macOS, or WSL terminal).
Quotes and a final unquoted & are supported. No pipelines, redirection,
expansion, scheduling simulation, memory simulation, or authentication.
Ctrl+C interrupts a foreground child; Ctrl+Z stops it. Use jobs, bg ID,
fg ID, or kill PID. Optional kill -STOP/-CONT/-KILL PID aids demonstrations.
Exit terminates and reaps this shell's tracked children.
"""
import os
import shlex
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Job:
    jid: int
    pid: int
    command: str
    state: str = 'Running'


class Shell:
    builtins = {'cd', 'pwd', 'exit', 'echo', 'clear', 'ls', 'cat', 'mkdir',
                'rmdir', 'rm', 'touch', 'kill', 'jobs', 'fg', 'bg'}

    def __init__(self):
        self.jobs = {}
        self.next_id = 1
        self.running = True
        self.tty = sys.stdin.isatty()
        self.fd = sys.stdin.fileno()
        self.pgid = os.getpgrp()
        if self.tty:
            # Wait until our launching shell places us in the foreground.
            while os.tcgetpgrp(self.fd) != os.getpgrp():
                os.killpg(os.getpgrp(), signal.SIGTTIN)
            for sig in (signal.SIGTTOU, signal.SIGTTIN, signal.SIGTSTP):
                signal.signal(sig, signal.SIG_IGN)
            try:
                os.setpgid(0, 0)
            except PermissionError:
                pass  # Already a session leader in a PTY.
            self.pgid = os.getpgrp()
            os.tcsetpgrp(self.fd, self.pgid)

    def status(self, job, status):
        if os.WIFSTOPPED(status):
            job.state = 'Stopped'
        elif os.WIFCONTINUED(status):
            job.state = 'Running'
        elif os.WIFEXITED(status):
            job.state = f'Done exit={os.WEXITSTATUS(status)}'
        elif os.WIFSIGNALED(status):
            job.state = f'Terminated signal={os.WTERMSIG(status)}'

    def reap(self):
        # Single owner of background wait status; avoids a SIGCHLD-handler race.
        for job in list(self.jobs.values()):
            while True:
                try:
                    pid, status = os.waitpid(job.pid, os.WNOHANG | os.WUNTRACED | os.WCONTINUED)
                except ChildProcessError:
                    self.jobs.pop(job.jid, None)
                    break
                if not pid:
                    break
                self.status(job, status)
                if job.state.startswith(('Done', 'Terminated')):
                    print(f'[{job.jid}] PID={job.pid} {job.state} {job.command}', flush=True)
                    self.jobs.pop(job.jid, None)
                    break

    def foreground(self, job, resume=False):
        try:
            if self.tty:
                os.tcsetpgrp(self.fd, job.pid)
            if resume:
                os.killpg(job.pid, signal.SIGCONT)
                job.state = 'Running'
            while True:
                try:
                    _, status = os.waitpid(job.pid, os.WUNTRACED)
                    break
                except InterruptedError:
                    continue
                except KeyboardInterrupt:
                    os.killpg(job.pid, signal.SIGINT)
            self.status(job, status)
        finally:
            if self.tty:
                os.tcsetpgrp(self.fd, self.pgid)
        print(f'[{job.jid}] PID={job.pid} {job.state} {job.command}', flush=True)
        if job.state != 'Stopped':
            self.jobs.pop(job.jid, None)

    def spawn(self, args, background, command):
        # A pipe gates exec until the parent has assigned terminal ownership.
        read_fd, write_fd = os.pipe()
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            pid = os.fork()
        except OSError:
            os.close(read_fd)
            os.close(write_fd)
            raise
        if pid == 0:
            try:
                os.close(write_fd)
                os.setpgid(0, 0)
                for sig in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP,
                            signal.SIGTTIN, signal.SIGTTOU, signal.SIGPIPE):
                    signal.signal(sig, signal.SIG_DFL)
                os.read(read_fd, 1)
                os.close(read_fd)
                if args[0] in self.builtins:
                    self.builtin(args)
                    sys.stdout.flush()
                    os._exit(0)
                os.execvp(args[0], args)
            except BaseException as exc:
                print(f'Error: {args[0]}: {exc}', file=sys.stderr, flush=True)
                os._exit(127 if isinstance(exc, FileNotFoundError) else 1)
        os.close(read_fd)
        try:
            os.setpgid(pid, pid)
        except (ProcessLookupError, PermissionError):
            pass
        job = Job(self.next_id, pid, command)
        self.next_id += 1
        self.jobs[job.jid] = job
        try:
            if not background and self.tty:
                os.tcsetpgrp(self.fd, pid)
            os.write(write_fd, b'1')
        finally:
            os.close(write_fd)
        if background:
            print(f'[{job.jid}] PID={pid} Running {command}', flush=True)
        else:
            self.foreground(job)

    @staticmethod
    def arity(args, minimum, maximum):
        if not minimum <= len(args) - 1 <= maximum:
            raise ValueError(f'{args[0]} expects {minimum} to {maximum} argument(s)')

    def builtin(self, args):
        cmd, *values = args
        if cmd in {'pwd', 'exit', 'clear', 'jobs'}:
            self.arity(args, 0, 0)
        if cmd == 'cd':
            self.arity(args, 0, 1)
            os.chdir(os.path.expanduser(values[0] if values else '~'))
        elif cmd == 'pwd':
            print(os.getcwd())
        elif cmd == 'exit':
            self.running = False
        elif cmd == 'echo':
            print(' '.join(values))
        elif cmd == 'clear':
            print('\033[2J\033[H', end='', flush=True)
        elif cmd == 'ls':
            self.arity(args, 0, 1)
            for item in sorted(Path(values[0] if values else '.').iterdir()):
                print(item.name + ('/' if item.is_dir() else ''))
        elif cmd == 'cat':
            self.arity(args, 1, 1000)
            for value in values:
                with open(value, encoding='utf-8') as stream:
                    print(stream.read(), end='')
        elif cmd in {'mkdir', 'rmdir', 'rm', 'touch'}:
            self.arity(args, 1, 1000)
            for value in values:
                path = Path(value)
                if cmd == 'mkdir':
                    path.mkdir()
                elif cmd == 'rmdir':
                    path.rmdir()
                elif cmd == 'rm':
                    path.unlink()
                else:
                    path.touch()
        elif cmd == 'kill':
            self.arity(args, 1, 2)
            sig = signal.SIGTERM
            if len(values) == 2:
                option = values.pop(0)
                if option not in {'-STOP', '-CONT', '-TERM', '-KILL', '-INT'}:
                    raise ValueError('signal must be -STOP, -CONT, -TERM, -KILL, or -INT')
                sig = getattr(signal, 'SIG' + option[1:])
            pid = int(values[0])
            if pid <= 0:
                raise ValueError('PID must be a positive integer')
            os.kill(pid, sig)
            print(f'Sent {signal.Signals(sig).name} to PID={pid}')
        elif cmd == 'jobs':
            self.reap()
            if not self.jobs:
                print('No background jobs')
            for job in self.jobs.values():
                print(f'[{job.jid}] PID={job.pid} {job.state} {job.command}')
        elif cmd in {'fg', 'bg'}:
            self.arity(args, 1, 1)
            jid = int(values[0].lstrip('%'))
            if jid not in self.jobs:
                raise ValueError(f'no active job {jid}')
            job = self.jobs[jid]
            if cmd == 'fg':
                self.foreground(job, resume=True)
            else:
                os.killpg(job.pid, signal.SIGCONT)
                job.state = 'Running'
                print(f'[{jid}] PID={job.pid} Running {job.command}')

    def execute(self, line):
        self.reap()
        # shlex preserves quoted ampersands as words; punctuation lexing is
        # combined with inspection of the original trailing character.
        line = line.strip()
        background = False
        if line.endswith('&'):
            try:
                shlex.split(line[:-1])
                background = True  # Fails for a quoted terminal ampersand.
                line = line[:-1].rstrip()
            except ValueError:
                pass
        args = shlex.split(line)
        if not args:
            if background:
                raise ValueError('missing command before &')
            return
        if any(word in {'|', '>', '<', ';', '&&', '||', '&'} for word in args):
            raise ValueError('only a trailing background & operator is supported')
        if background and args[0] in {'cd', 'exit', 'jobs', 'fg', 'bg', 'kill'}:
            raise ValueError(f'{args[0]} must run in the foreground')
        if args[0] in self.builtins and not background:
            self.builtin(args)
        else:
            self.spawn(args, background, line)

    def cleanup(self):
        # Exit policy: shut down owned child groups, then reap every child.
        for job in list(self.jobs.values()):
            try:
                os.killpg(job.pid, signal.SIGTERM)
                os.killpg(job.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 0.3
        while self.jobs and time.monotonic() < deadline:
            self.reap()
            time.sleep(0.01)
        for job in list(self.jobs.values()):
            try:
                os.killpg(job.pid, signal.SIGKILL)
                os.waitpid(job.pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass
        self.jobs.clear()

    def run(self):
        print('Deliverable 1 shell | jobs, fg ID, bg ID | exit to close')
        try:
            while self.running:
                try:
                    self.reap()
                    line = input('d1> ')
                    self.execute(line)
                except EOFError:
                    break
                except KeyboardInterrupt:
                    print()
                except (OSError, ValueError, UnicodeError) as exc:
                    print(f'Error: {exc}', flush=True)
        finally:
            self.cleanup()


if __name__ == '__main__':
    if os.name != 'posix':
        sys.exit('This shell requires Linux, macOS, or WSL, with POSIX job control.')
    Shell().run()
