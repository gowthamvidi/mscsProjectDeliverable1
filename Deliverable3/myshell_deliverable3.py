#!/usr/bin/env python3
"""
myshell - Advanced Shell Simulation, Deliverable 3

Includes:
- Shell built-in commands
- Process management and job control
- Memory management using paging
- FIFO and LRU page replacement algorithms
- Process synchronization using mutexes and semaphores
- Producer-Consumer synchronization demonstration

Run:  python3 myshell.py
"""

import os
import sys
import stat
import time
import shlex
import signal
import shutil
import termios
from memory_sync import Paging, bounded_buffer_demo, comparison

try:
    import readline  # line editing + history for input()
except ImportError:  # pragma: no cover - readline is optional
    readline = None

SHELL_NAME = "myshell"

# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
BLUE, GREEN, RED, YELLOW, CYAN, BOLD, RESET = (
    "\033[34m", "\033[32m", "\033[91m", "\033[33m", "\033[36m", "\033[1m", "\033[0m")


ERROR_COUNT = 0


def err(msg):
    """All shell error messages go to stderr with a consistent prefix."""
    global ERROR_COUNT
    ERROR_COUNT += 1
    print(f"{RED}{SHELL_NAME}: {msg}{RESET}", file=sys.stderr, flush=True)


class ShellError(Exception):
    """Raised by built-ins for user errors; caught by the main loop."""


# ----------------------------------------------------------------------------
# Job table
# ----------------------------------------------------------------------------
class Job:
    RUNNING, STOPPED, DONE, KILLED = "Running", "Stopped", "Done", "Terminated"

    def __init__(self, job_id, pid, cmdline, background):
        self.id = job_id
        self.pid = pid          # one process per job, so pgid == pid
        self.pgid = pid
        self.cmd = cmdline
        self.background = background
        self.status = Job.RUNNING
        self.exit_info = ""
        self.tmodes = None      # terminal modes saved when the job stops
        self.notified = False

    def describe(self):
        state = self.status
        if self.status == Job.DONE and self.exit_info:
            state = f"Done({self.exit_info})" if self.exit_info != "0" else "Done"
        if self.status == Job.KILLED:
            state = self.exit_info or "Terminated"
        amp = " &" if self.status == Job.RUNNING else ""
        return f"{state:<14}{self.pid:<8}{self.cmd}{amp}"


class JobTable:
    def __init__(self):
        self.jobs = {}          # job_id -> Job
        self.order = []         # most recently touched job id is last

    def add(self, pid, cmdline, background):
        job_id = max(self.jobs, default=0) + 1
        job = Job(job_id, pid, cmdline, background)
        self.jobs[job_id] = job
        self.touch(job_id)
        return job

    def touch(self, job_id):
        if job_id in self.order:
            self.order.remove(job_id)
        self.order.append(job_id)

    def remove(self, job_id):
        self.jobs.pop(job_id, None)
        if job_id in self.order:
            self.order.remove(job_id)

    def marker(self, job_id):
        if self.order and self.order[-1] == job_id:
            return "+"
        if len(self.order) > 1 and self.order[-2] == job_id:
            return "-"
        return " "

    def current(self):
        return self.jobs[self.order[-1]] if self.order else None

    def by_pid(self, pid):
        for job in self.jobs.values():
            if job.pid == pid:
                return job
        return None

    def resolve(self, spec):
        """Turn 'N', '%N', '%+', '%-' or nothing into a Job."""
        if spec is None or spec in ("%", "%+", "%%"):
            job = self.current()
            if job is None:
                raise ShellError("no current job")
            return job
        if spec == "%-":
            if len(self.order) < 2:
                raise ShellError("no previous job")
            return self.jobs[self.order[-2]]
        text = spec[1:] if spec.startswith("%") else spec
        if not text.isdigit():
            raise ShellError(f"{spec}: invalid job id (use a number such as 1 or %1)")
        job = self.jobs.get(int(text))
        if job is None:
            raise ShellError(f"{spec}: no such job")
        return job


# ----------------------------------------------------------------------------
# The shell
# ----------------------------------------------------------------------------
class Shell:
    def __init__(self):
        self.jobs = JobTable()
        self.paging = Paging()
        self.last_status = 0
        self.trace = False
        self.exit_warned = False
        self.interactive = sys.stdin.isatty()
        self.shell_pgid = os.getpgrp()
        self.tty_fd = sys.stdin.fileno()
        self.shell_tmodes = None
        self.builtins = {
            "cd": self.bi_cd, "pwd": self.bi_pwd, "exit": self.bi_exit,
            "echo": self.bi_echo, "clear": self.bi_clear, "ls": self.bi_ls,
            "cat": self.bi_cat, "mkdir": self.bi_mkdir, "rmdir": self.bi_rmdir,
            "rm": self.bi_rm, "touch": self.bi_touch, "kill": self.bi_kill,
            "jobs": self.bi_jobs, "fg": self.bi_fg, "bg": self.bi_bg,
            "help": self.bi_help, "trace": self.bi_trace,
            "mem": self.bi_mem, "sync": self.bi_sync,
        }
        # Built-ins that change the shell's own state must run in the shell
        # process itself and can never be backgrounded.
        self.shell_state_builtins = {"cd", "exit", "jobs", "fg", "bg", "kill", "trace", "mem", "sync"}
        self.init_terminal()

    # -- terminal / signal setup ---------------------------------------------
    def init_terminal(self):
        if not self.interactive:
            return
        # Wait until we are in the foreground of the terminal.
        while os.tcgetpgrp(self.tty_fd) != os.getpgrp():
            os.kill(-os.getpgrp(), signal.SIGTTIN)
        # The shell ignores job-control signals so that Ctrl+Z / Ctrl+\ only
        # affect the foreground job, and so it can call tcsetpgrp() freely.
        for sig in (signal.SIGTSTP, signal.SIGTTIN, signal.SIGTTOU, signal.SIGQUIT):
            signal.signal(sig, signal.SIG_IGN)
        # Put the shell in its own process group and take the terminal.
        try:
            os.setpgid(0, 0)
        except PermissionError:
            pass  # already a session/group leader
        self.shell_pgid = os.getpgrp()
        os.tcsetpgrp(self.tty_fd, self.shell_pgid)
        self.shell_tmodes = termios.tcgetattr(self.tty_fd)

    def give_terminal_to(self, pgid):
        if self.interactive:
            os.tcsetpgrp(self.tty_fd, pgid)

    def take_terminal_back(self, job=None):
        if not self.interactive:
            return
        os.tcsetpgrp(self.tty_fd, self.shell_pgid)
        if job is not None:
            job.tmodes = termios.tcgetattr(self.tty_fd)
        termios.tcsetattr(self.tty_fd, termios.TCSADRAIN, self.shell_tmodes)

    def tr(self, msg):
        if self.trace:
            print(f"{CYAN}[trace] {msg}{RESET}", flush=True)

    # -- parsing --------------------------------------------------------------
    def parse(self, line):
        """Split a command line into (argv, background)."""
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        try:
            tokens = list(lexer)
        except ValueError as e:              # e.g. unmatched quote
            raise ShellError(f"syntax error: {e}")
        background = False
        if tokens and tokens[-1] == "&":
            background = True
            tokens.pop()
        for tok in tokens:
            if tok == "&" or (tok and set(tok) <= set("&")):
                raise ShellError("syntax error near '&' ('&' is only allowed at the end of a command)")
            if tok and set(tok) <= set("|;<>()"):
                raise ShellError(f"syntax error near '{tok}' (pipes, redirection and ';' "
                                 f"are not part of Deliverable 1)")
        if background and not tokens:
            raise ShellError("syntax error near '&' (no command before it)")
        return [self.expand(t) for t in tokens], background

    def expand(self, word):
        word = word.replace("$?", str(self.last_status)).replace("$$", str(os.getpid()))
        return os.path.expanduser(os.path.expandvars(word))

    # -- main loop ------------------------------------------------------------
    def prompt(self):
        cwd = os.getcwd()
        home = os.path.expanduser("~")
        if cwd == home or cwd.startswith(home + os.sep):
            cwd = "~" + cwd[len(home):]
        if not self.interactive:
            return ""
        # \001 \002 tell readline the colour codes take no screen space.
        g, b, r = f"\001{GREEN}{BOLD}\002", f"\001{BLUE}{BOLD}\002", f"\001{RESET}\002"
        return f"{g}{SHELL_NAME}{r}:{b}{cwd}{r}$ "

    def run(self):
        if self.interactive:
            print(f"{BOLD}{SHELL_NAME}{RESET} - Advanced Shell Simulation (Deliverable 3). "
                  f"Shell PID {os.getpid()}. Type 'help' for commands.")
        while True:
            self.reap_children()
            self.report_finished_jobs()
            try:
                line = input(self.prompt())
            except EOFError:                       # Ctrl+D
                if self.interactive:
                    print("exit")
                try:
                    self.bi_exit([])
                except ShellError as e:
                    err(str(e))
                continue
            except KeyboardInterrupt:              # Ctrl+C at the prompt
                print("^C")
                self.last_status = 130
                continue
            line = line.strip()
            if not line:
                continue
            try:
                self.execute(line)
            except ShellError as e:
                err(str(e))
                self.last_status = 1
            except KeyboardInterrupt:              # Ctrl+C during a built-in
                print("^C")
                self.last_status = 130

    def execute(self, line):
        argv, background = self.parse(line)
        if not argv:
            return
        name = argv[0]
        cmdline = " ".join(shlex.quote(a) if " " in a else a for a in argv)
        if name in self.builtins:
            if background and name in self.shell_state_builtins:
                raise ShellError(f"{name}: shell built-in cannot run in the background")
            if background:
                self.launch(argv, cmdline, background=True, builtin=True)
            else:
                self.tr(f"'{name}' is a built-in: running inside the shell process "
                        f"(PID {os.getpid()}), no fork()")
                before = ERROR_COUNT
                self.builtins[name](argv[1:])
                self.last_status = 0 if ERROR_COUNT == before else 1
        else:
            self.launch(argv, cmdline, background=background, builtin=False)

    # -- process creation -----------------------------------------------------
    def launch(self, argv, cmdline, background, builtin):
        self.tr(f"fork() called by shell PID {os.getpid()}")
        try:
            pid = os.fork()
        except OSError as e:
            raise ShellError(f"fork failed: {e.strerror}")

        if pid == 0:                                     # ---- child ----
            try:
                os.setpgid(0, 0)                         # new process group
                if not background and self.interactive:
                    os.tcsetpgrp(self.tty_fd, os.getpid())
                for sig in (signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP,
                            signal.SIGTTIN, signal.SIGTTOU, signal.SIGCHLD,
                            signal.SIGPIPE):
                    signal.signal(sig, signal.SIG_DFL)
                if background:
                    # Background jobs must not steal keyboard input.
                    devnull = os.open(os.devnull, os.O_RDONLY)
                    os.dup2(devnull, 0)
                    os.close(devnull)
                if builtin:
                    try:
                        self.builtins[argv[0]](argv[1:])
                        code = 0
                    except ShellError as e:
                        err(str(e))
                        code = 1
                    sys.stdout.flush()
                    os._exit(code)
                if self.trace:
                    print(f"{CYAN}[trace] child PID {os.getpid()} (parent {os.getppid()}): "
                          f"setpgid -> pgid {os.getpgrp()}, execvp('{argv[0]}', {argv}){RESET}",
                          flush=True)
                os.execvp(argv[0], argv)                 # never returns on success
            except FileNotFoundError:
                err(f"{argv[0]}: command not found")
                os._exit(127)
            except PermissionError:
                err(f"{argv[0]}: permission denied")
                os._exit(126)
            except OSError as e:
                err(f"{argv[0]}: {e.strerror}")
                os._exit(126)
            except BaseException:
                os._exit(1)
            os._exit(1)

        # ---- parent ----
        try:
            os.setpgid(pid, pid)       # done in both processes to avoid a race
        except (PermissionError, ProcessLookupError):
            pass
        self.tr(f"fork() returned child PID {pid} to the parent")
        job = self.jobs.add(pid, cmdline, background)
        if background:
            print(f"[{job.id}] {pid}", flush=True)
            self.tr("background job: parent does NOT call waitpid(); prompt returns at once")
            self.last_status = 0
        else:
            self.give_terminal_to(pid)
            self.wait_foreground(job)

    def wait_foreground(self, job):
        """Block until the foreground job exits or stops (Ctrl+Z)."""
        self.tr(f"parent calls waitpid({job.pid}, WUNTRACED) and blocks")
        while True:
            try:
                _, status = os.waitpid(job.pid, os.WUNTRACED)
            except ChildProcessError:
                status = None
                break
            if os.WIFSTOPPED(status) or os.WIFEXITED(status) or os.WIFSIGNALED(status):
                break
        self.take_terminal_back(job)

        if status is None:
            self.jobs.remove(job.id)
            return
        if os.WIFSTOPPED(status):
            job.status = Job.STOPPED
            job.background = True
            self.jobs.touch(job.id)
            self.last_status = 128 + os.WSTOPSIG(status)
            print(f"\n[{job.id}]{self.jobs.marker(job.id)}  Stopped                 {job.cmd}")
            self.tr(f"waitpid: child {job.pid} STOPPED by "
                    f"{signal.Signals(os.WSTOPSIG(status)).name}; kept in job table")
            return
        if os.WIFEXITED(status):
            self.last_status = os.WEXITSTATUS(status)
            self.tr(f"waitpid: child {job.pid} exited with status {self.last_status}; "
                    f"zombie reaped")
        else:
            sig = os.WTERMSIG(status)
            self.last_status = 128 + sig
            if sig not in (signal.SIGINT, signal.SIGPIPE):
                print(f"{signal.Signals(sig).name} {job.cmd}")
            else:
                print()
            self.tr(f"waitpid: child {job.pid} killed by {signal.Signals(sig).name}")
        self.jobs.remove(job.id)

    def reap_children(self):
        """Non-blocking check of every job: catches exits, stops and continues."""
        flags = os.WNOHANG | os.WUNTRACED | os.WCONTINUED
        for job in list(self.jobs.jobs.values()):
            if job.status in (Job.DONE, Job.KILLED):
                continue
            try:
                pid, status = os.waitpid(job.pid, flags)
            except ChildProcessError:
                job.status, job.exit_info = Job.DONE, ""
                continue
            if pid == 0:
                continue
            if os.WIFEXITED(status):
                job.status = Job.DONE
                job.exit_info = str(os.WEXITSTATUS(status))
            elif os.WIFSIGNALED(status):
                job.status = Job.KILLED
                job.exit_info = signal.Signals(os.WTERMSIG(status)).name
            elif os.WIFSTOPPED(status):
                job.status = Job.STOPPED
                print(f"[{job.id}]{self.jobs.marker(job.id)}  Stopped                 {job.cmd}")
            elif os.WIFCONTINUED(status):
                job.status = Job.RUNNING

    def report_finished_jobs(self):
        for job in sorted(self.jobs.jobs.values(), key=lambda j: j.id):
            if job.status in (Job.DONE, Job.KILLED):
                print(f"[{job.id}]{self.jobs.marker(job.id)}  {job.describe()}")
                self.jobs.remove(job.id)

    # ------------------------------------------------------------------------
    # Built-in commands
    # ------------------------------------------------------------------------
    def bi_cd(self, args):
        if len(args) > 1:
            raise ShellError("cd: too many arguments")
        target = args[0] if args else os.path.expanduser("~")
        if target == "-":
            target = os.environ.get("OLDPWD")
            if not target:
                raise ShellError("cd: OLDPWD not set")
            print(target)
        try:
            old = os.getcwd()
            os.chdir(target)
            os.environ["OLDPWD"], os.environ["PWD"] = old, os.getcwd()
        except FileNotFoundError:
            raise ShellError(f"cd: {target}: No such file or directory")
        except NotADirectoryError:
            raise ShellError(f"cd: {target}: Not a directory")
        except PermissionError:
            raise ShellError(f"cd: {target}: Permission denied")

    def bi_pwd(self, args):
        if args:
            raise ShellError("pwd: too many arguments")
        print(os.getcwd())

    def bi_exit(self, args):
        code = self.last_status
        if args:
            if len(args) > 1:
                raise ShellError("exit: too many arguments")
            try:
                code = int(args[0]) & 0xFF
            except ValueError:
                raise ShellError(f"exit: {args[0]}: numeric argument required")
        self.reap_children()
        live = [j for j in self.jobs.jobs.values() if j.status in (Job.RUNNING, Job.STOPPED)]
        if live and not self.exit_warned:
            self.exit_warned = True
            kinds = {j.status.lower() for j in live}
            raise ShellError(f"exit: there are {' and '.join(sorted(kinds))} jobs "
                             f"(type 'jobs' to see them, or 'exit' again to quit anyway)")
        for job in live:                    # like bash: hang up remaining jobs
            for sig in (signal.SIGHUP, signal.SIGCONT):
                try:
                    os.kill(-job.pgid, sig)
                except ProcessLookupError:
                    pass
        print("Goodbye!")
        sys.exit(code)

    def bi_echo(self, args):
        newline = True
        if args and args[0] == "-n":
            newline, args = False, args[1:]
        print(" ".join(args), end="\n" if newline else "", flush=True)

    def bi_clear(self, args):
        print("\033[H\033[2J\033[3J", end="", flush=True)

    def bi_ls(self, args):
        show_all = long = False
        paths = []
        for a in args:
            if a.startswith("-") and len(a) > 1:
                for ch in a[1:]:
                    if ch == "a":
                        show_all = True
                    elif ch == "l":
                        long = True
                    else:
                        raise ShellError(f"ls: invalid option -- '{ch}' (supported: -a, -l)")
            else:
                paths.append(a)
        paths = paths or ["."]
        for i, path in enumerate(paths):
            if not os.path.lexists(path):
                err(f"ls: cannot access '{path}': No such file or directory")
                continue
            if not os.path.isdir(path):
                self._ls_print([(path, path)], long)
                continue
            if len(paths) > 1:
                print(("\n" if i else "") + f"{path}:")
            try:
                names = os.listdir(path)
            except PermissionError:
                err(f"ls: cannot open directory '{path}': Permission denied")
                continue
            if show_all:
                names += [".", ".."]
            names = sorted((n for n in names if show_all or not n.startswith(".")),
                           key=lambda n: n.lower().lstrip("."))
            self._ls_print([(n, os.path.join(path, n)) for n in names], long)

    def _ls_print(self, entries, long):
        def colour(name, full):
            if os.path.isdir(full):
                return f"{BLUE}{BOLD}{name}{RESET}"
            if os.access(full, os.X_OK):
                return f"{GREEN}{BOLD}{name}{RESET}"
            return name
        if long:
            for name, full in entries:
                st = os.lstat(full)
                when = time.strftime("%b %d %H:%M", time.localtime(st.st_mtime))
                print(f"{stat.filemode(st.st_mode)} {st.st_nlink:>2} {st.st_size:>8} "
                      f"{when} {colour(name, full)}")
            return
        if not entries:
            return
        width = shutil.get_terminal_size((80, 24)).columns
        col = max(len(n) for n, _ in entries) + 2
        per_row = max(1, width // col)
        for i in range(0, len(entries), per_row):
            row = entries[i:i + per_row]
            print("".join(colour(n, f) + " " * (col - len(n)) for n, f in row).rstrip())

    def bi_cat(self, args):
        if not args:
            raise ShellError("cat: missing file operand")
        for name in args:
            try:
                with open(name, "rb") as fh:
                    while chunk := fh.read(65536):
                        sys.stdout.buffer.write(chunk)
                sys.stdout.flush()
            except FileNotFoundError:
                err(f"cat: {name}: No such file or directory")
            except IsADirectoryError:
                err(f"cat: {name}: Is a directory")
            except PermissionError:
                err(f"cat: {name}: Permission denied")

    def bi_mkdir(self, args):
        parents = "-p" in args
        names = [a for a in args if a != "-p"]
        if not names:
            raise ShellError("mkdir: missing operand")
        for name in names:
            try:
                os.makedirs(name, exist_ok=True) if parents else os.mkdir(name)
            except FileExistsError:
                err(f"mkdir: cannot create directory '{name}': File exists")
            except FileNotFoundError:
                err(f"mkdir: cannot create directory '{name}': No such file or directory "
                    f"(use -p to create parents)")
            except PermissionError:
                err(f"mkdir: cannot create directory '{name}': Permission denied")

    def bi_rmdir(self, args):
        if not args:
            raise ShellError("rmdir: missing operand")
        for name in args:
            try:
                os.rmdir(name)
            except FileNotFoundError:
                err(f"rmdir: failed to remove '{name}': No such file or directory")
            except NotADirectoryError:
                err(f"rmdir: failed to remove '{name}': Not a directory")
            except OSError as e:
                if e.errno in (39, 66):   # ENOTEMPTY (Linux / macOS)
                    err(f"rmdir: failed to remove '{name}': Directory not empty")
                else:
                    err(f"rmdir: failed to remove '{name}': {e.strerror}")

    def bi_rm(self, args):
        if not args:
            raise ShellError("rm: missing operand")
        for name in args:
            if os.path.isdir(name) and not os.path.islink(name):
                err(f"rm: cannot remove '{name}': Is a directory (use rmdir)")
                continue
            try:
                os.remove(name)
            except FileNotFoundError:
                err(f"rm: cannot remove '{name}': No such file or directory")
            except PermissionError:
                err(f"rm: cannot remove '{name}': Permission denied")

    def bi_touch(self, args):
        if not args:
            raise ShellError("touch: missing file operand")
        for name in args:
            try:
                with open(name, "a"):
                    pass
                os.utime(name, None)        # update access + modification time
            except FileNotFoundError:
                err(f"touch: cannot touch '{name}': No such file or directory")
            except IsADirectoryError:
                os.utime(name, None)
            except PermissionError:
                err(f"touch: cannot touch '{name}': Permission denied")

    def bi_kill(self, args):
        sig = signal.SIGTERM
        if args and args[0].startswith("-") and len(args[0]) > 1:
            spec = args[0][1:].upper()
            try:
                sig = signal.Signals(int(spec)) if spec.isdigit() else \
                    signal.Signals[spec if spec.startswith("SIG") else "SIG" + spec]
            except (KeyError, ValueError):
                raise ShellError(f"kill: {args[0]}: invalid signal specification")
            args = args[1:]
        if not args:
            raise ShellError("kill: usage: kill [-SIGNAL] pid | %job_id")
        for target in args:
            if target.startswith("%"):
                job = self.jobs.resolve(target)
                pid, pgid = job.pid, job.pgid
            else:
                if not target.lstrip("-").isdigit():
                    err(f"kill: {target}: arguments must be process or job IDs")
                    continue
                pid = int(target)
                if pid <= 0:
                    err(f"kill: {target}: invalid process ID")
                    continue
                if pid == os.getpid():
                    err("kill: refusing to kill the shell itself (use 'exit')")
                    continue
                job = self.jobs.by_pid(pid)
                pgid = job.pgid if job else None
            try:
                # Signal the whole process group of a job, else the single pid.
                os.kill(-pgid if pgid else pid, sig)
                self.tr(f"kill({'-' + str(pgid) if pgid else pid}, {sig.name}) sent")
                if job and sig == signal.SIGCONT:
                    job.status = Job.RUNNING
                elif job and job.status == Job.STOPPED and sig not in (
                        signal.SIGSTOP, signal.SIGTSTP, signal.SIGKILL):
                    # A stopped process only acts on most signals once it runs
                    # again, so wake it up (bash does the same).
                    os.kill(-pgid, signal.SIGCONT)
            except ProcessLookupError:
                err(f"kill: ({target}) - No such process")
            except PermissionError:
                err(f"kill: ({target}) - Operation not permitted")
        time.sleep(0.05)       # give the signal a moment so 'jobs' is accurate
        self.reap_children()

    def bi_jobs(self, args):
        self.reap_children()
        if not self.jobs.jobs:
            print("No jobs.")
            return
        print(f"{'JOB':<7}{'STATE':<14}{'PID':<8}COMMAND")
        for job in sorted(self.jobs.jobs.values(), key=lambda j: j.id):
            print(f"[{job.id}]{self.jobs.marker(job.id):<3} {job.describe()}")
        # Finished jobs are listed once and then removed.
        for job in list(self.jobs.jobs.values()):
            if job.status in (Job.DONE, Job.KILLED):
                self.jobs.remove(job.id)

    def bi_fg(self, args):
        if len(args) > 1:
            raise ShellError("fg: too many arguments")
        self.reap_children()
        job = self.jobs.resolve(args[0] if args else None)
        if job.status in (Job.DONE, Job.KILLED):
            self.jobs.remove(job.id)
            raise ShellError(f"fg: job {job.id} has already finished")
        print(job.cmd, flush=True)
        job.background = False
        self.jobs.touch(job.id)
        self.give_terminal_to(job.pgid)
        if job.tmodes and self.interactive:
            termios.tcsetattr(self.tty_fd, termios.TCSADRAIN, job.tmodes)
        self.tr(f"tcsetpgrp(tty, {job.pgid}); kill(-{job.pgid}, SIGCONT)")
        os.kill(-job.pgid, signal.SIGCONT)
        job.status = Job.RUNNING
        self.wait_foreground(job)

    def bi_bg(self, args):
        self.reap_children()
        targets = args or [None]
        for spec in targets:
            job = self.jobs.resolve(spec)
            if job.status == Job.RUNNING:
                err(f"bg: job {job.id} already in background")
                continue
            if job.status != Job.STOPPED:
                err(f"bg: job {job.id} has already finished")
                continue
            self.tr(f"kill(-{job.pgid}, SIGCONT); shell keeps the terminal")
            os.kill(-job.pgid, signal.SIGCONT)
            job.status = Job.RUNNING
            job.background = True
            self.jobs.touch(job.id)
            print(f"[{job.id}]{self.jobs.marker(job.id)} {job.cmd} &")

    def bi_trace(self, args):
        if args and args[0] in ("on", "off"):
            self.trace = args[0] == "on"
        elif args:
            raise ShellError("trace: usage: trace [on|off]")
        print(f"process tracing is {'on' if self.trace else 'off'}")

    def bi_mem(self, args):
        """mem init [frames] [FIFO|LRU]; mem access PID PAGE; mem free PID; mem status; mem compare."""
        if not args:
            raise ShellError('mem: usage: mem init [frames] [FIFO|LRU] | access PID PAGE | free PID | status | compare')
        try:
            op=args[0]
            if op=='init' and len(args) in (1,2,3):
                self.paging=Paging(int(args[1]) if len(args)>1 else 3, algorithm=args[2] if len(args)>2 else 'FIFO')
                print(self.paging.status())
            elif op=='access' and len(args)==3: print(self.paging.access(args[1],int(args[2])))
            elif op=='free' and len(args)==2: print(self.paging.deallocate(args[1]))
            elif op=='status' and len(args)==1: print(self.paging.status())
            elif op=='compare' and len(args)==1:
                for algo,faults,hits,evictions,_,_ in comparison():
                    print(f'{algo}: faults={faults} hits={hits} evictions={evictions}')
            else: raise ShellError('mem: invalid arguments (try: mem init 3 FIFO)')
        except ValueError as e: raise ShellError(f'mem: {e}')

    def bi_sync(self, args):
        """sync demo [items] [buffer_size] -- mutex + counting semaphores."""
        if not args or args[0]!='demo' or len(args)>3:
            raise ShellError('sync: usage: sync demo [items] [capacity]')
        try: print(bounded_buffer_demo(int(args[1]) if len(args)>1 else 6,
                                        int(args[2]) if len(args)>2 else 2))
        except ValueError as e: raise ShellError(f'sync: {e}')

    def bi_help(self, args):
        print(f"""{BOLD}{SHELL_NAME} built-in commands{RESET}
  cd [dir]           change directory (no arg = home, '-' = previous)
  pwd                print working directory
  echo [-n] [text]   print text ($VAR, $? and ~ are expanded)
  clear              clear the screen
  ls [-a] [-l] [dir] list directory contents
  cat file...        print file contents
  mkdir [-p] dir...  create directories
  rmdir dir...       remove empty directories
  rm file...         remove files
  touch file...      create file / update timestamp
  kill [-SIG] pid|%n send a signal (default SIGTERM)
  jobs               list background and stopped jobs
  fg [%n]            bring job to the foreground
  bg [%n]            resume a stopped job in the background
  trace [on|off]     show fork/exec/wait activity
  mem init [N] [FIFO|LRU]; mem access PID PAGE; mem free PID; mem status; mem compare
  sync demo [items] [buffer capacity]  synchronized producer-consumer
  exit [n]           leave the shell
Any other command is run as an external program with fork() + execvp().
Append '&' to run in the background. Ctrl+Z stops the foreground job,
Ctrl+C interrupts it.""")


def main():
    shell = Shell()
    try:
        shell.run()
    except SystemExit:
        raise


if __name__ == "__main__":
    main()
