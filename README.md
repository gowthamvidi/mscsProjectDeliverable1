# Advanced Shell Simulation — Deliverable 1

## Overview

This project implements a basic Unix-like shell using Python. It supports built-in commands, foreground and background execution, process tracking, basic job control, and error handling.

## Project Files

| File | Description |
| --- | --- |
| `deliverable1_shell.py` | Python source code for the shell. |
| `Deliverable_1_Final_Submission.docx` | Report containing implementation details and execution evidence. |
| `README.md` | Setup instructions and usage examples. |

## Requirements

- Python 3.10 or newer.
- Linux, macOS, or Windows Subsystem for Linux (WSL).
- An interactive terminal.
- The external `sleep` command for process-management demonstrations.

No third-party Python packages are required.

## Running the Shell

Open a terminal in the project directory and run:

```bash
python3 deliverable1_shell.py
```

The shell displays this prompt:

```text
d1>
```

### Running on Windows Using WSL

open a WSL terminal and run:

```bash
python3 deliverable1_shell.py
```

This implementation requires POSIX interfaces and cannot run directly in native Windows Command Prompt or PowerShell.

## Supported Commands

| Command | Description |
| --- | --- |
| `cd [directory]` | Change the working directory. |
| `pwd` | Display the working directory. |
| `exit` | Close the shell and clean up tracked child processes. |
| `echo [text]` | Print text. |
| `clear` | Clear the terminal screen. |
| `ls [directory]` | List directory contents. |
| `cat filename` | Display a UTF-8 text file. |
| `mkdir directory` | Create a directory. |
| `rmdir directory` | Remove an empty directory. |
| `rm filename` | Remove a file. |
| `touch filename` | Create an empty file or update its timestamp. |
| `kill PID` | Send a termination signal to a process. |
| `jobs` | Display tracked background and stopped jobs. |
| `fg JOB_ID` | Bring a job into the foreground and resume it. |
| `bg JOB_ID` | Resume a job in the background. |

Commands support their basic documented forms rather than the complete option sets of standard Unix utilities.

## Usage Examples

Enter the following commands at the `d1>` prompt.

### Built-in Commands

```text
pwd
echo "Hello from my shell"
mkdir demo
cd demo
touch sample.txt
ls
cat sample.txt
rm sample.txt
cd ..
rmdir demo
```

The newly created `sample.txt` is empty, so `cat` produces no visible content. Use an existing text file to demonstrate visible file contents.

### Foreground Execution

```text
sleep 2
```

The shell waits for the process to finish before displaying another prompt.

### Background Execution

```text
sleep 60 &
jobs
```

The trailing `&` starts the command in the background and returns the prompt immediately.

### Job Control

Use the actual job ID and PID displayed by your shell:

```text
kill -STOP PID
jobs
bg JOB_ID
fg JOB_ID
```

Replace `PID` and `JOB_ID` with their numeric values.

- Press **Ctrl+Z** to stop a foreground child process.
- Run `bg JOB_ID` to resume it in the background.
- Run `fg JOB_ID` to bring it into the foreground.
- Press **Ctrl+C** to interrupt a foreground child process.
- Run `kill PID` to terminate a background process.

`fg` and `bg` use job IDs; `kill` uses a process ID.

### Error Handling

```text
not_a_real_command_xyz
cat absent_file.txt
fg 999
kill abc
mkdir
echo "Shell continues after errors"
```

Assuming the file and job do not exist, these examples demonstrate error messages for invalid commands, missing files, unknown jobs, invalid PIDs, and missing arguments.

### Exiting

```text
exit
```

You can also press **Ctrl+D** to close the shell.

## Process Management Design

- `shlex.split()` parses commands and quoted arguments.
- Foreground built-ins execute in the parent shell.
- `os.fork()` creates child processes.
- `os.execvp()` runs external commands.
- Process groups and `os.tcsetpgrp()` manage terminal ownership.
- `os.waitpid()` waits for foreground processes and checks background process status.
- A `Job` record stores each job’s ID, PID, command, and state.
- Tracked states include Running, Stopped, Done, and Terminated.
- On exit, the shell terminates and reaps tracked child processes.

## Optional Automated Demonstration

If `deliverable1_demo.py` has been added beside the shell source, run it from the host terminal:

```bash
python3 deliverable1_demo.py
```

The script demonstrates built-ins, process management, job control, signals, error handling, clear, and exit. It saves the session output to:

```text
deliverable1_demo_transcript.txt
```

Take direct terminal screenshots separately for the assignment’s screenshot requirement.

## Limitations

- Pipelines and input/output redirection are unsupported.
- Environment-variable expansion and wildcard expansion are unsupported.
- Background completion notifications may be delayed while the shell waits for input.
- Full terminal-mode preservation for interactive full-screen programs is not implemented.
- Scheduling algorithms, memory simulation, and authentication are outside Deliverable 1’s scope.

## Report

See `Deliverable_1_Final_Submission.docx` for the design explanation, execution evidence, implementation challenges, and proposed improvements.
