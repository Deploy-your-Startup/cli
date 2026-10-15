# Terminal design

The CLI follows the website's active homepage and the branded installer from
[website PR #21](https://github.com/philipp-lein/deploy-your-startup-website/pull/21).
The design is implemented with Click; no rendering dependency is added.

| Element | Treatment |
| --- | --- |
| Brand | `>_ deploy your startup` and `Ideas in. Startups out.` match the installer. |
| Accent | Homepage `#9cbedc`: RGB `156;190;220` on truecolor terminals, xterm color 110 on 256-color terminals, terminal foreground otherwise. |
| Text | Inherit the terminal's foreground, background and font. Use bold only for the wordmark and summary headings. |
| Progress | One short `2/6  Project setup` line. Preserve the detailed command output below it. |
| Status | One leading symbol per message. Green `✓`, red `✗` and yellow `!` keep their existing success, failure and warning meanings. Informational marks use the brand accent. |
| Summary | Plain labelled rows, then the next commands or Actions link. Long paths and URLs are preserved; no fixed-width frame or colored text block. |
| Motion | Keep the existing brief rocket reveal once, before bootstrap questions. The settled wordmark is static, matching the installer. Provider setup does not replay the animation. |

`src/cli/terminal_style.py` owns terminal capability checks and the palette.
Bootstrap, doctor and Hetzner output share it. A nonempty `NO_COLOR`, `TERM=dumb`,
and redirected output disable styling and animation. A nonempty `CI` or
`STARTUP_NO_ANIMATION` disables motion; colors still depend on an output terminal.
This preserves doctor's status colors when explicitly run in a CI pseudo-terminal.
These settings govern the CLI's own output; subprocesses retain control of their
logs.

The summary distinguishes configuration from a verified deployment. It retains
the BYOS deploy-key and infrastructure/deploy commands, directs shared-cluster
operations to the owner, and leaves DNS/HTTPS verification visible for hosted
deployments. Paths in copyable shell commands are quoted.

Validation should exercise the real CLI in a terminal and with redirected output,
including truecolor/256-color/basic terminals, motion opt-out, cancellation and
provider-specific summaries. Local provider stand-ins do not verify real OAuth,
macOS rendering or a live deployment.
