using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;

namespace Jz2440.Control.Core
{
    internal sealed class CodexApplicationProviderSession : IDisposable
    {
        private readonly global::ISerialTransport transport;
        private readonly ILogger logger;
        private readonly Action<Exception> faulted;
        private readonly CancellationTokenSource cancellation = new CancellationTokenSource();
        private readonly global::BoardApplicationSession boardSession;
        private Task loop;
        private global::IQuotaProvider provider;
        private bool disposed;

        public CodexApplicationProviderSession(global::ISerialTransport serial, ILogger appLogger, Action<Exception> onFault)
        {
            transport = serial;
            logger = appLogger ?? new NullLogger();
            faulted = onFault;
            boardSession = new global::BoardApplicationSession(transport, ReadQuota);
        }

        public void Start(string initialData)
        {
            loop = Task.Run(() => Run(initialData), cancellation.Token);
        }

        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            cancellation.Cancel();
            // RealCodexProvider has a bounded 15 second RPC wait. Do not send
            // a lifecycle stop frame until its reader has left the shared
            // transport, otherwise two writers can overlap during a switch.
            try { if (loop != null) loop.Wait(17000); } catch { }
            DisposeProvider();
            cancellation.Dispose();
        }

        private void Run(string initialData)
        {
            string pending = initialData;
            int loggedResponses = 0;
            try
            {
                while (!cancellation.IsCancellationRequested && !boardSession.Stopped)
                {
                    try
                    {
                        string incoming = pending;
                        pending = null;
                        if (string.IsNullOrEmpty(incoming)) incoming = transport.ReadAvailable();
                        if (ContainsStopMarker(incoming))
                        {
                            PushBack(incoming);
                            return;
                        }
                        boardSession.Consume(incoming);
                        if (boardSession.Responses > loggedResponses)
                        {
                            logger.Info("Codex provider response sent.");
                            loggedResponses = boardSession.Responses;
                        }
                    }
                    catch (Exception error)
                    {
                        logger.Error("Codex provider request failed; waiting for the next request.", error);
                        DisposeProvider();
                        if (cancellation.IsCancellationRequested) break;
                        Thread.Sleep(100);
                    }
                    Thread.Sleep(50);
                }
            }
            catch (Exception error)
            {
                logger.Error("Codex provider session stopped unexpectedly.", error);
                if (faulted != null && !cancellation.IsCancellationRequested) faulted(error);
            }
            finally
            {
                DisposeProvider();
            }
        }

        private global::QuotaSnapshot ReadQuota()
        {
            if (provider == null) provider = new global::RealCodexProvider();
            return provider.ReadQuota();
        }

        private void DisposeProvider()
        {
            if (provider == null) return;
            try { provider.Dispose(); } catch { }
            provider = null;
        }

        private void PushBack(string incoming)
        {
            BufferedSerialTransport buffered = transport as BufferedSerialTransport;
            if (buffered != null) buffered.PushBack(incoming);
        }

        private static bool ContainsStopMarker(string incoming)
        {
            return !string.IsNullOrEmpty(incoming) && incoming.IndexOf("<APPSTOP|codex-monitor|", StringComparison.Ordinal) >= 0;
        }
    }

    internal sealed class RunBoardApplicationProviderSession : IDisposable
    {
        private readonly global::ISerialTransport transport;
        private readonly AppConfiguration configuration;
        private readonly ILogger logger;
        private readonly Action<Exception> faulted;
        private readonly CancellationTokenSource cancellation = new CancellationTokenSource();
        private RunBoardFrameSource source;
        private Task loop;
        private bool disposed;
        private int sequence;

        public RunBoardApplicationProviderSession(global::ISerialTransport serial, AppConfiguration appConfiguration,
                                                  ILogger appLogger, Action<Exception> onFault)
        {
            transport = serial;
            configuration = appConfiguration ?? new AppConfiguration();
            logger = appLogger ?? new NullLogger();
            faulted = onFault;
        }

        public void Start()
        {
            source = new RunBoardFrameSource(configuration, logger);
            source.Start();
            SendFrame();
            logger.Info("RunBoard provider first frame sent.");
            loop = Task.Run(Run, cancellation.Token);
        }

        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            cancellation.Cancel();
            if (source != null) source.Dispose();
            // ReadFrame is bounded by the worker read timeout. Join the loop
            // before the caller sends the next lifecycle frame so this
            // provider cannot write after a switch has started.
            try { if (loop != null) loop.Wait(32000); } catch { }
            cancellation.Dispose();
        }

        private void Run()
        {
            try
            {
                int interval = Math.Max(250, configuration.RunBoardFrameIntervalMs);
                while (!cancellation.IsCancellationRequested)
                {
                    string incoming = transport.ReadAvailable();
                    if (!string.IsNullOrEmpty(incoming) && incoming.IndexOf("<APPSTOP|runboard|", StringComparison.Ordinal) >= 0)
                    {
                        PushBack(incoming);
                        return;
                    }
                    int waited = 0;
                    while (waited < interval && !cancellation.IsCancellationRequested)
                    {
                        Thread.Sleep(Math.Min(100, interval - waited));
                        waited += Math.Min(100, interval - waited);
                        incoming = transport.ReadAvailable();
                        if (!string.IsNullOrEmpty(incoming) && incoming.IndexOf("<APPSTOP|runboard|", StringComparison.Ordinal) >= 0)
                        {
                            PushBack(incoming);
                            return;
                        }
                    }
                    if (!cancellation.IsCancellationRequested) SendFrame();
                }
            }
            catch (Exception error)
            {
                logger.Error("RunBoard provider session stopped unexpectedly.", error);
                if (faulted != null && !cancellation.IsCancellationRequested) faulted(error);
            }
        }

        private void SendFrame()
        {
            string frame = source.ReadFrame(sequence++);
            transport.Write(frame);
        }

        private void PushBack(string incoming)
        {
            BufferedSerialTransport buffered = transport as BufferedSerialTransport;
            if (buffered != null) buffered.PushBack(incoming);
        }
    }

    internal sealed class RunBoardFrameSource : IDisposable
    {
        private readonly AppConfiguration configuration;
        private readonly ILogger logger;
        private readonly CancellationTokenSource readCancellation = new CancellationTokenSource();
        private Process process;
        private bool disposed;

        public RunBoardFrameSource(AppConfiguration appConfiguration, ILogger appLogger)
        {
            configuration = appConfiguration ?? new AppConfiguration();
            logger = appLogger ?? new NullLogger();
        }

        public void Start()
        {
            string script = ResolveScript(configuration.RunBoardStateScript);
            if (script == null)
                throw new InvalidOperationException("RunBoard state provider script is not configured.");

            ProcessStartInfo info = new ProcessStartInfo
            {
                FileName = string.IsNullOrWhiteSpace(configuration.RunBoardPython) ? "python" : configuration.RunBoardPython,
                WorkingDirectory = FindRoot(script),
                UseShellExecute = false,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true
            };
            info.ArgumentList.Add("-u");
            info.ArgumentList.Add(script);
            info.ArgumentList.Add("--live-worker");
            process = Process.Start(info);
            if (process == null) throw new InvalidOperationException("RunBoard state provider did not start.");
            process.ErrorDataReceived += delegate { };
            process.BeginErrorReadLine();
        }

        public string ReadFrame(int sequence)
        {
            Process current = process;
            if (disposed || current == null) throw new ObjectDisposedException(GetType().Name);
            if (current.HasExited) throw new InvalidOperationException("RunBoard state provider exited.");
            current.StandardInput.WriteLine(sequence.ToString());
            current.StandardInput.Flush();
            Task<string> read = current.StandardOutput.ReadLineAsync(readCancellation.Token).AsTask();
            if (!read.Wait(30000))
            {
                Dispose();
                throw new TimeoutException("RunBoard state provider timed out.");
            }
            string line = read.Result;
            if (string.IsNullOrEmpty(line) || !line.StartsWith("RB1|", StringComparison.Ordinal))
                throw new InvalidOperationException("RunBoard state provider returned an invalid frame.");
            return line + "\n";
        }

        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            readCancellation.Cancel();
            Process current = process;
            try { if (current != null && !current.HasExited) current.Kill(); } catch { }
            try { if (current != null) current.Dispose(); } catch { }
            process = null;
            readCancellation.Dispose();
        }

        private static string ResolveScript(string configured)
        {
            if (!string.IsNullOrWhiteSpace(configured))
                return File.Exists(configured) ? Path.GetFullPath(configured) : null;
            string[] starts = { AppContext.BaseDirectory, Directory.GetCurrentDirectory() };
            foreach (string start in starts)
            {
                DirectoryInfo directory = new DirectoryInfo(start);
                while (directory != null)
                {
                    string candidate = Path.Combine(directory.FullName, "apps", "runboard", "host", "runboard_state_cli.py");
                    if (File.Exists(candidate)) return candidate;
                    directory = directory.Parent;
                }
            }
            return null;
        }

        private static string FindRoot(string script)
        {
            DirectoryInfo directory = new DirectoryInfo(Path.GetDirectoryName(script));
            while (directory != null && !File.Exists(Path.Combine(directory.FullName, "config", "config.example.json")))
                directory = directory.Parent;
            return directory == null ? Directory.GetCurrentDirectory() : directory.FullName;
        }
    }
}
