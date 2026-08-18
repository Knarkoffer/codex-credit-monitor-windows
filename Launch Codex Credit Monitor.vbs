Option Explicit

Dim shell, filesystem, scriptPath, command, exitCode
Set shell = CreateObject("WScript.Shell")
Set filesystem = CreateObject("Scripting.FileSystemObject")

scriptPath = filesystem.BuildPath(filesystem.GetParentFolderName(WScript.ScriptFullName), "Launch Codex Credit Monitor.cmd")
command = "cmd.exe /d /c " & Chr(34) & Chr(34) & scriptPath & Chr(34) & " --background" & Chr(34)
exitCode = shell.Run(command, 0, True)

If exitCode <> 0 Then
    MsgBox "Codex Credit Monitor could not start. Install Python 3.12 or later from python.org, then try again.", vbOKOnly + vbExclamation, "Codex Credit Monitor"
End If
