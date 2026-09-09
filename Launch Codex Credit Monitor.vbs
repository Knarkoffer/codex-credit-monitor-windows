Option Explicit

Dim shell, filesystem, scriptPath, errorFile, command, exitCode, errorMessage, errorText
Set shell = CreateObject("WScript.Shell")
Set filesystem = CreateObject("Scripting.FileSystemObject")

scriptPath = filesystem.BuildPath(filesystem.GetParentFolderName(WScript.ScriptFullName), "Launch Codex Credit Monitor.cmd")
errorFile = shell.ExpandEnvironmentStrings("%TEMP%") & "\CodexCreditMonitor-launch-error.txt"
If filesystem.FileExists(errorFile) Then filesystem.DeleteFile errorFile, True
shell.Environment("PROCESS")("CCM_ERROR_FILE") = errorFile
command = "cmd.exe /d /c " & Chr(34) & Chr(34) & scriptPath & Chr(34) & " --background" & Chr(34)
exitCode = shell.Run(command, 0, True)

If exitCode <> 0 Then
    errorMessage = "Codex Credit Monitor could not start. Run Launch Codex Credit Monitor.cmd --diagnose from Command Prompt to see the full error."
    If filesystem.FileExists(errorFile) Then
        Set errorText = filesystem.OpenTextFile(errorFile, 1)
        errorMessage = errorText.ReadAll
        errorText.Close
        filesystem.DeleteFile errorFile, True
    End If
    MsgBox errorMessage, vbOKOnly + vbExclamation, "Codex Credit Monitor"
End If
