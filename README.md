# Codex Credit Monitor for Windows + WSL

Codex Credit Monitor is a small Windows desktop app that shows your remaining
Codex credits, how quickly you are using them, and when they reset. It refreshes
when it opens and then every 15 minutes.

## Before you start

You need:

- Windows with [Python 3.12 or later](https://www.python.org/downloads/windows/)
  installed. During installation, select **Add Python to PATH**.
- A ChatGPT account with Codex access.
- A current Codex login. If you normally use Codex in WSL, open your WSL
  terminal and run `codex login` first.

## Start the monitor

Double-click **`Launch Codex Credit Monitor.vbs`** in this folder. It opens the
monitor without leaving a command window on screen.

On the first launch, the launcher prepares the app automatically. This can take
a minute or two; later launches open directly. If Windows asks whether to allow
the script to run, allow it only when you obtained this folder from a source you
trust.

## Set it up

Select **Settings** in the monitor after it opens.

- Set your time zone with an IANA name, such as `Europe/Stockholm` or
  `America/New_York`.
- Set your working day. The start and end values are minutes after midnight:
  `480` is 08:00 and `1020` is 17:00.
- If you have several WSL distributions, choose the one where you ran
  `codex login`. Leave it blank to use Windows' default WSL distribution.
- Enable notifications if you want Windows to alert you when your spending is
  critically ahead of your selected pace.

The monitor prefers a native Windows Codex login at
`%USERPROFILE%\.codex\auth.json`. Otherwise, it reads the login from the selected
WSL distribution. It never changes either login.

## Your data

The monitor stores its settings and local usage history in
`%LOCALAPPDATA%\CodexCreditMonitor`. If that location is unavailable, it uses
`%APPDATA%\CodexCreditMonitor` instead.

Use **Delete history** in the app to remove the stored observations and
notification state. Your settings and Codex login are kept.

## Troubleshooting

**The monitor does not open**

Install Python 3.12 or later, then double-click the launcher again. If Python
was updated after an earlier failed setup, delete the `.venv` folder next to the
launcher and try again.

**The monitor cannot find your Codex login**

In the WSL distribution you use for Codex, run `codex login`. If you have more
than one distribution, select it in **Settings**, then refresh the monitor.

**The monitor says your account is not eligible or shows HTTP 402**

Check that the logged-in ChatGPT account has an eligible Codex plan or credit
allocation. Run `codex login` again if you need to choose a different account.

**The displayed pace is wrong**

Review the time zone and working-day values in **Settings**. The pace calculation
uses working hours only, so a wrong time zone or schedule changes the result.

## Privacy

To refresh the display, the app requests Codex usage data from ChatGPT. It does
not write OAuth tokens, response bodies, account IDs, or credit values to logs.
If a refresh fails, the last successful display and history remain available.
