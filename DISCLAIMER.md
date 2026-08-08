# Disclaimer

By installing or using Refuel you accept the terms below.
(The Windows app also asks you to tick an explicit agreement box on first launch.)

## 1. Unofficial tool

Refuel is an unofficial tool. It is not affiliated with, sponsored by, or endorsed by Anthropic, OpenAI, Cursor, GitHub, or any other company. All trademarks belong to their respective owners.

## 2. Everything shown is an estimate

Token limits, reset times and weekly figures are **estimates** calculated from log files on your own machine. Providers do not publish exact token quotas, so these numbers can differ from the real ones. Accuracy and completeness are not guaranteed — treat them as a rough guide, not as a billing record.

## 3. Data handling (phone sync)

- Phone sync is **off by default (opt-in)** and runs only if you turn it on.
- What is sent: **token counts, timestamps and agent names only.** Your code, conversations, prompts and API keys are never sent.
- Status data is **end-to-end encrypted with AES-GCM** and relayed to your own device through ntfy. The developer holds no decryption key and cannot read it.
- Alert text is relayed in plaintext so it can be displayed, but the messages queued on the server contain no figures — only generic notices such as "refueled".
- You can turn phone sync off at any time. Regenerating the topic and key in settings immediately invalidates any existing pairing.

## 4. No warranty

This software is provided **"AS IS"**, without warranty of any kind, express or implied. You are solely responsible for any consequences of using it, direct or indirect — including lost work or decisions made on a misread limit. The developer accepts no liability.

## 5. Third-party services

- **ntfy.sh** is used as the relay when phone sync is on. Its availability and policies are outside the developer's control.
- **GitHub** is contacted once a day to check for new releases (read-only). This can be turned off in settings.

## 6. Your responsibility

Refuel reads local log files. Make sure you have the right to read the logs on the machine you run it on, and comply with the terms of service of the AI provider you use.

## 7. Changes

These terms may change with future versions. When the text changes materially, the app asks for your agreement again on the next launch.

---

Full source code: <https://github.com/nohseongmin/Refuel>
Licensed under the [MIT License](LICENSE).
