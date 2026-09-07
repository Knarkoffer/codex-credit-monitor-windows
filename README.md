# Codex Credit Monitor for Windows + WSL

Codex Credit Monitor is a small Windows desktop app that shows your Codex plan
usage or enterprise credit allocation, how quickly you are using it, and when
the current window resets. It refreshes when it opens and then every 15 minutes.

## Before you start

You need:

- Windows with [Python 3.12 or later](https://www.python.org/downloads/windows/)
  installed. During installation, select **Add Python to PATH**.
- A ChatGPT account with Codex access.
- A current Codex login. If you normally use Codex in WSL, open your WSL
  terminal and run `codex login` first.

## Get the app

This repository currently distributes the monitor as Python source rather than
as a prebuilt installer or `.exe` file. Download the complete project folder in
one of these ways:

- On the [GitHub repository](https://github.com/Knarkoffer/codex-credit-monitor-windows),
  select **Code**, then **Download ZIP**, and extract the ZIP to a permanent
  folder.
- Or clone it with Git:

  ```powershell
  git clone https://github.com/Knarkoffer/codex-credit-monitor-windows.git
  cd codex-credit-monitor-windows
  ```

Keep the source folder and both `Launch Codex Credit Monitor` files together.
The launcher runs the app from that folder.

## Start the monitor

Double-click **`Launch Codex Credit Monitor.vbs`** in this folder. It opens the
monitor without leaving a command window on screen.

On the first launch, the launcher creates a private `.venv` Python environment
inside the project folder and installs the app and its dependencies there. This
can take a minute or two; later launches open directly. Neither `.venv` nor the
temporary `build` folder belongs in the download: the launcher recreates what it
needs from the tracked source files. If Windows asks whether to allow the script
to run, allow it only when you obtained this folder from a source you trust.

Minimizing the monitor hides its window and leaves a tray icon in the Windows
notification area near the clock. Double-click the icon or choose **Open** from
its menu to restore the window. Its tooltip shows the current period's rounded
utilization percentage. Choose **Quit** from the tray menu, the app's Quit
button, or the window's close button to stop the monitor.

The first time it is minimized during each run, the monitor shows a notification
explaining where to find the icon. Windows may put new icons behind the **^**
button in the notification area.

Only one monitor can run at a time. Starting it again restores and focuses the
existing monitor instead of opening another copy.

The current application version appears at the bottom of the monitor. The
launcher compares the installed version with [`VERSION`](VERSION) and updates
the private environment from the local project folder when they differ. See
[`CHANGELOG.md`](CHANGELOG.md) for the changes in each version.

### Manual setup and start

If you prefer PowerShell, or need to see installation errors, run these commands
from the extracted or cloned project folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m codex_credit_monitor_windows
```

The generated `build` directory is only temporary packaging output. The app
does not run from it, so it can be deleted and is intentionally excluded from
Git.

## Set it up

Select **Settings** in the monitor after it opens.

- Set your time zone with an IANA name, such as `Europe/Stockholm` or
  `America/New_York`.
- Choose **Personal** or **Work** under **Usage mode**. Personal spreads your
  usage pace evenly across the full reset period, including evenings and
  weekends, with no working hours to configure. Work counts Monday through
  Friday within your chosen working hours.
- In Work mode, choose when your working day starts and ends.
- If you have several WSL distributions, choose the one where you ran
  `codex login`. Leave it blank to use Windows' default WSL distribution.
- Enable notifications if you want Windows to alert you when your usage is
  critically ahead of your selected pace.

On the first launch, the monitor defaults to Work if the Windows computer is
joined to a domain, and Personal otherwise. If domain membership cannot be
determined, it uses Personal. This initial choice is saved immediately; later
launches use your saved mode even if domain membership changes. You can change
the mode in Settings at any time. Existing settings are preserved, including
Work mode for settings saved before the mode selector was added.

Either mode works with either account type. Switching modes
updates the current period's pace and graph immediately, keeps your saved
working hours and usage history, and applies to future periods. Completed
periods retain their saved mode. Switching itself does not send an alert.

The monitor detects the account plan from the Codex login and authenticated
usage response. Personal plans show the longest available rate-limit window
(normally the weekly limit); enterprise usage-based plans show their individual
credit allocation.

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

Install Python 3.12 or later, then double-click the launcher again. The
launcher supports Python installed through the Windows `py` launcher or a
`python` command on your PATH. If Python was updated after an earlier failed
setup, delete the `.venv` folder next to the launcher and try again. To see
full setup errors, open Command Prompt in the project folder and run:

```cmd
"Launch Codex Credit Monitor.cmd" --background
```

**The monitor cannot find your Codex login**

If the selected WSL distribution is stopped, start that distribution and then
refresh the monitor.

In the WSL distribution you use for Codex, run `codex login`. If you have more
than one distribution, select it in **Settings**, then refresh the monitor.

**The monitor says your account is not eligible or shows HTTP 402**

Check that the logged-in ChatGPT account has an eligible Codex plan or credit
allocation. Run `codex login` again if you need to choose a different account.

**The displayed pace is wrong**

Review **Usage mode** in **Settings**. Personal uses elapsed time across the
entire reset period. Work uses weekday working hours only, so check the time
zone and working-day values when using that mode.

## Privacy

To refresh the display, the app requests Codex usage data from ChatGPT. It does
not write OAuth tokens, response bodies, account IDs, or credit values to logs.
If a refresh fails, the last successful display and history remain available.
