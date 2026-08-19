using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

[assembly: System.Reflection.AssemblyTitle("Agent4Market")]
[assembly: System.Reflection.AssemblyDescription("One-click launcher for the independent Agent4Market runtime")]
[assembly: System.Reflection.AssemblyCompany("WorkFlow_Market")]
[assembly: System.Reflection.AssemblyProduct("Agent4Market")]
[assembly: System.Reflection.AssemblyVersion("0.4.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("0.4.0.0")]

namespace Agent4MarketLauncher
{
    internal sealed class PythonCommand
    {
        public string FileName;
        public string Prefix;
    }

    internal static class Program
    {
        private const int WorkbenchPort = 8765;
        private static readonly object LogLock = new object();
        private static Process WorkbenchProcess;
        private static Process AgentProcess;
        private static bool OwnsWorkbench;
        private static string LauncherLog;
        private static int Stopping;

        [STAThread]
        private static int Main(string[] arguments)
        {
            Console.OutputEncoding = Encoding.UTF8;
            string root = FindProjectRoot();
            if (root == null)
                return Fail("Agent4Market.exe must stay inside the installed Agent4Market directory.");

            bool selfTest = Contains(arguments, "--self-test");
            bool noBrowser = selfTest || Contains(arguments, "--no-browser");
            bool created;
            using (Mutex instance = new Mutex(true, "Local\\Agent4Market-" + RootHash(root), out created))
            {
                if (!created)
                {
                    if (!noBrowser && WorkbenchHealthy()) OpenBrowser();
                    return Fail("Agent4Market is already running for this directory.");
                }
                try
                {
                    LauncherLog = Path.Combine(root, ".pi", "director-runtime", "launcher.log");
                    Directory.CreateDirectory(Path.GetDirectoryName(LauncherLog));
                    AppDomain.CurrentDomain.ProcessExit += delegate { Cleanup(); };
                    Console.CancelKeyPress += delegate(object sender, ConsoleCancelEventArgs eventArguments)
                    {
                        eventArguments.Cancel = true;
                        Cleanup();
                        Environment.Exit(130);
                    };

                    PythonCommand python = FindPython();
                    if (python == null)
                        return Fail("Python 3.11+ was not found. Run scripts\\setup-windows.ps1 first.");

                    if (!WorkbenchHealthy())
                    {
                        if (WorkbenchPortOccupied())
                            return Fail("Port 8765 is already used by another program; the launcher will not take it over.");
                        WorkbenchProcess = StartWorkbench(root, python);
                        OwnsWorkbench = true;
                        if (!WaitForWorkbench(WorkbenchProcess, 20000))
                            return Fail("The local workbench did not start. See .pi\\director-runtime\\launcher.log.");
                    }

                    if (selfTest)
                    {
                        if (RunPiVersion(root) != 0) return Fail("Pi startup self-test failed.");
                        Console.WriteLine("Agent4Market self-test passed: workbench HTTP 200, Pi available.");
                        return 0;
                    }

                    if (!noBrowser) OpenBrowser();
                    Console.WriteLine("Agent4Market is ready. Workbench: http://127.0.0.1:8765");
                    Console.WriteLine("Exit Pi or press Ctrl+C to stop this launcher.");
                    AgentProcess = StartAgent(root, WithoutLauncherArguments(arguments));
                    AgentProcess.WaitForExit();
                    return AgentProcess.ExitCode;
                }
                catch (Exception error)
                {
                    Log("launcher", error.ToString());
                    return Fail(error.Message);
                }
                finally
                {
                    Cleanup();
                    try { instance.ReleaseMutex(); } catch (ApplicationException) { }
                }
            }
        }

        private static string FindProjectRoot()
        {
            DirectoryInfo current = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
            for (int depth = 0; depth < 4 && current != null; depth += 1, current = current.Parent)
                if (IsProjectRoot(current.FullName)) return current.FullName;
            return null;
        }

        private static bool IsProjectRoot(string path)
        {
            return Directory.Exists(path)
                && File.Exists(Path.Combine(path, "scripts", "start-windows.ps1"))
                && File.Exists(Path.Combine(path, "ui", "server.py"))
                && File.Exists(Path.Combine(path, "node_modules", ".bin", "pi.CMD"));
        }

        private static string RootHash(string root)
        {
            using (SHA256 sha = SHA256.Create())
            {
                byte[] digest = sha.ComputeHash(Encoding.UTF8.GetBytes(root.ToUpperInvariant()));
                StringBuilder value = new StringBuilder();
                for (int index = 0; index < 8; index += 1) value.Append(digest[index].ToString("x2"));
                return value.ToString();
            }
        }

        private static PythonCommand FindPython()
        {
            List<PythonCommand> candidates = new List<PythonCommand>();
            string configured = Environment.GetEnvironmentVariable("AGENT4MARKET_PYTHON");
            if (!String.IsNullOrWhiteSpace(configured))
                candidates.Add(new PythonCommand { FileName = configured, Prefix = "" });
            candidates.Add(new PythonCommand { FileName = "python.exe", Prefix = "" });
            candidates.Add(new PythonCommand { FileName = "py.exe", Prefix = "-3.11" });
            foreach (PythonCommand candidate in candidates)
            {
                string output;
                if (!RunCapture(candidate.FileName, JoinArguments(candidate.Prefix, "--version"), 8000, out output)) continue;
                Match version = Regex.Match(output, @"Python\s+(\d+)\.(\d+)");
                int major;
                int minor;
                if (version.Success && Int32.TryParse(version.Groups[1].Value, out major)
                    && Int32.TryParse(version.Groups[2].Value, out minor)
                    && (major > 3 || (major == 3 && minor >= 11))) return candidate;
            }
            return null;
        }

        private static Process StartWorkbench(string root, PythonCommand python)
        {
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = python.FileName;
            info.Arguments = JoinArguments(python.Prefix, Quote(Path.Combine(root, "ui", "server.py")), "--port", WorkbenchPort.ToString());
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.EnvironmentVariables["PYTHONUTF8"] = "1";
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            Process process = new Process();
            process.StartInfo = info;
            process.EnableRaisingEvents = true;
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs data) { if (data.Data != null) Log("workbench", data.Data); };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs data) { if (data.Data != null) Log("workbench-error", data.Data); };
            if (!process.Start()) throw new InvalidOperationException("Could not start the local workbench.");
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
            return process;
        }

        private static bool WaitForWorkbench(Process process, int timeoutMilliseconds)
        {
            Stopwatch timer = Stopwatch.StartNew();
            while (timer.ElapsedMilliseconds < timeoutMilliseconds)
            {
                if (WorkbenchHealthy()) return true;
                if (process != null && process.HasExited) return false;
                Thread.Sleep(250);
            }
            return false;
        }

        private static bool WorkbenchHealthy()
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + WorkbenchPort + "/");
                request.Method = "GET";
                request.Timeout = 1000;
                request.ReadWriteTimeout = 1000;
                request.Proxy = null;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                    return response.StatusCode == HttpStatusCode.OK
                        && !String.IsNullOrEmpty(response.Server)
                        && response.Server.StartsWith("DirectorWorkbench/", StringComparison.Ordinal);
            }
            catch (WebException) { return false; }
        }

        private static bool WorkbenchPortOccupied()
        {
            try
            {
                using (TcpClient client = new TcpClient())
                {
                    IAsyncResult pending = client.BeginConnect(IPAddress.Loopback, WorkbenchPort, null, null);
                    bool connected;
                    using (WaitHandle wait = pending.AsyncWaitHandle) connected = wait.WaitOne(500);
                    if (!connected) return false;
                    client.EndConnect(pending);
                    return client.Connected;
                }
            }
            catch (SocketException) { return false; }
        }

        private static Process StartAgent(string root, string[] forwarded)
        {
            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = "powershell.exe";
            info.WorkingDirectory = root;
            info.UseShellExecute = false;
            info.CreateNoWindow = false;
            List<string> values = new List<string>();
            values.Add("-NoLogo");
            values.Add("-NoProfile");
            values.Add("-ExecutionPolicy");
            values.Add("Bypass");
            values.Add("-File");
            values.Add(Quote(Path.Combine(root, "scripts", "start-windows.ps1")));
            if (!Contains(forwarded, "--approve") && !Contains(forwarded, "-a")
                && !Contains(forwarded, "--no-approve") && !Contains(forwarded, "-na"))
                values.Add(Quote("--approve"));
            foreach (string argument in forwarded) values.Add(Quote(argument));
            info.Arguments = String.Join(" ", values.ToArray());
            Process process = new Process();
            process.StartInfo = info;
            if (!process.Start()) throw new InvalidOperationException("Could not start Pi.");
            return process;
        }

        private static int RunPiVersion(string root)
        {
            string output;
            bool ok = RunCapture(
                "powershell.exe",
                JoinArguments("-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", Quote(Path.Combine(root, "scripts", "start-windows.ps1")), "--version"),
                30000,
                out output,
                root);
            if (!ok || !Regex.IsMatch(output, @"\b0\.84\.2\b"))
            {
                Log("pi-self-test", output);
                return 1;
            }
            return 0;
        }

        private static bool RunCapture(string fileName, string arguments, int timeout, out string output)
        {
            return RunCapture(fileName, arguments, timeout, out output, null);
        }

        private static bool RunCapture(string fileName, string arguments, int timeout, out string output, string workingDirectory)
        {
            output = "";
            try
            {
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = fileName;
                info.Arguments = arguments;
                info.WorkingDirectory = workingDirectory ?? AppDomain.CurrentDomain.BaseDirectory;
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                using (Process process = Process.Start(info))
                {
                    if (process == null) return false;
                    string standardOutput = process.StandardOutput.ReadToEnd();
                    string standardError = process.StandardError.ReadToEnd();
                    if (!process.WaitForExit(timeout))
                    {
                        StopProcessTree(process);
                        return false;
                    }
                    output = standardOutput + "\n" + standardError;
                    return process.ExitCode == 0;
                }
            }
            catch (Exception) { return false; }
        }

        private static void OpenBrowser()
        {
            try { Process.Start(new ProcessStartInfo("http://127.0.0.1:" + WorkbenchPort + "/") { UseShellExecute = true }); }
            catch (Exception error) { Log("browser", error.Message); }
        }

        private static string[] WithoutLauncherArguments(string[] arguments)
        {
            List<string> result = new List<string>();
            foreach (string argument in arguments)
                if (argument != "--no-browser" && argument != "--self-test") result.Add(argument);
            return result.ToArray();
        }

        private static bool Contains(string[] values, string expected)
        {
            foreach (string value in values) if (value == expected) return true;
            return false;
        }

        private static string JoinArguments(params string[] values)
        {
            List<string> present = new List<string>();
            foreach (string value in values) if (!String.IsNullOrWhiteSpace(value)) present.Add(value);
            return String.Join(" ", present.ToArray());
        }

        private static string Quote(string value)
        {
            return "\"" + (value ?? "").Replace("\"", "\\\"") + "\"";
        }

        private static void Log(string source, string message)
        {
            try
            {
                lock (LogLock)
                {
                    if (String.IsNullOrEmpty(LauncherLog)) return;
                    File.AppendAllText(LauncherLog, DateTimeOffset.Now.ToString("o") + " [" + source + "] " + message + Environment.NewLine, Encoding.UTF8);
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        private static int Fail(string message)
        {
            Console.Error.WriteLine("Agent4Market: " + message);
            return 2;
        }

        private static void Cleanup()
        {
            if (Interlocked.Exchange(ref Stopping, 1) != 0) return;
            if (AgentProcess != null && !AgentProcess.HasExited) StopProcessTree(AgentProcess);
            if (OwnsWorkbench && WorkbenchProcess != null && !WorkbenchProcess.HasExited) StopProcessTree(WorkbenchProcess);
        }

        private static void StopProcessTree(Process process)
        {
            try
            {
                if (process == null || process.HasExited) return;
                ProcessStartInfo info = new ProcessStartInfo();
                info.FileName = "taskkill.exe";
                info.Arguments = "/PID " + process.Id + " /T /F";
                info.UseShellExecute = false;
                info.CreateNoWindow = true;
                info.RedirectStandardOutput = true;
                info.RedirectStandardError = true;
                using (Process killer = Process.Start(info))
                    if (killer != null) killer.WaitForExit(8000);
            }
            catch (Exception error)
            {
                Log("cleanup", error.Message);
                try { if (!process.HasExited) process.Kill(); } catch (Exception) { }
            }
        }
    }
}
