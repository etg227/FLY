# Tool hub integration (development preview)

FLY can keep its source files separate from persistent user data:

```text
python main.py --data-dir C:\Users\you\AppData\Local\ToolHub\apps\fly\data
```

`--data-dir` overrides `FLY_DATA_DIR`. Without either, the portable layout is
unchanged. Built-in rules and VERSION still come from the source directory;
private, core and runtime come from the data directory. No user files are
automatically moved. Back up the old directories before copying them to a new
data root while FLY is stopped.

The preview tool hub reads `hub-tool.json` from the selected commit. FLY uses
Python 3.12 with Tcl/Tk and the standard library; the whole application must not
be elevated. FLY retains responsibility for verifying and elevating Mihomo.

For a managed launch, append `--hub-session` with a fresh 32-character lowercase
hex session ID. Once initialization finishes, FLY atomically writes
`runtime/hub/<session>/ready.json` with schema=1, session, pid and state=ready.
The manager must match both session and its own child PID before accepting it.

The manager writes `command.json` atomically in the same session directory:

```json
{"schema": 1, "session": "0123456789abcdef0123456789abcdef", "action": "stop"}
```

`show` restores the window. `stop` calls FLY's normal exit path on the Tk thread,
including proxy restoration, core shutdown and saving UI state. A stop request
is not proof of successful shutdown: wait for the owned process to exit. Never
replace a running deployment on timeout. Managed launches do not schedule
replacement of a standalone launcher. The readiness file is removed on exit.

This is a lifecycle integration, not the complete FLY package reorganization.
Existing launcher and Release compatibility are retained during migration.
