# Security Policy

This workbench keeps agent trace data on your machine. Use the guidance here to report problems safely.

## Supported scope

The project reports security issues for the latest release. Older releases receive no fixes.

## Reporting a vulnerability

Do not open a public issue for a vulnerability.

Send the details in a private report. Use the GitHub security tab for this repository.

Include these items in your report.

- The affected version number.
- The steps to reproduce the problem.
- The expected behavior.
- The observed behavior.
- Any logs that show the problem.

You should receive a reply within seven days. Keep the report private until a fix ships.

## Data handling

The workbench stores trace data in a local SQLite file. It does not send data to a hosted service.

WAL mode keeps the journal in local `-wal` and `-shm` files beside the database.

OpenTelemetry stays local by default. Do not export local spans unless you control the collector.

Replay runs local handler scripts from your configuration. Review a handler before you run it. The side-effect guard does not inspect handler code.

## Supported versions

| Version | Supported |
| ------- | --------- |
| 0.8.x   | Yes       |
| 0.7.x   | No        |
| 0.6.x   | No        |
| 0.5.x   | No        |
