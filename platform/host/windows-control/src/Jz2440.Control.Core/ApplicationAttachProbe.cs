using System;
using System.Text;
using System.Threading;

namespace Jz2440.Control.Core
{
    public sealed class ApplicationAttachProbeResult
    {
        public ApplicationAttachProbeResult(string appId, string initialData)
        {
            AppId = appId;
            InitialData = initialData;
        }

        public string AppId { get; private set; }
        public string InitialData { get; private set; }
    }

    // Detects an already-running target without sending a lifecycle stop/start
    // command. Qtopia is handled by BoardController's console marker probe;
    // this helper is only reached after that probe failed.
    public static class ApplicationAttachProbe
    {
        // RunBoard answers an RB1-shaped parse attempt with RBDBG, but only
        // draws after a valid frame. This deliberately malformed, non-rendering
        // probe therefore identifies RunBoard without changing the LCD state.
        private const string RunBoardProbeFrame = "RB1|L=1|V=1|CRC=0000\n";

        public static ApplicationAttachProbeResult TryDetect(
            global::ISerialTransport transport, AppConfiguration configuration, ILogger logger)
        {
            if (transport == null) throw new ArgumentNullException("transport");
            int timeout = Math.Max(6000, configuration == null ? 0 : configuration.OperationTimeoutMs);
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeout);
            StringBuilder pending = new StringBuilder();

            try
            {
                transport.Write(RunBoardProbeFrame);
            }
            catch (Exception error)
            {
                if (logger != null) logger.Error("Application attach probe write failed.", error);
                return null;
            }

            while (DateTime.UtcNow < deadline)
            {
                string incoming = transport.ReadAvailable();
                if (!string.IsNullOrEmpty(incoming))
                {
                    pending.Append(incoming);
                    ApplicationAttachProbeResult result = ReadLines(pending);
                    if (result != null) return result;
                }
                Thread.Sleep(25);
            }
            return ReadLines(pending);
        }

        private static ApplicationAttachProbeResult ReadLines(StringBuilder pending)
        {
            while (true)
            {
                int newline = pending.ToString().IndexOf('\n');
                if (newline < 0) return null;
                string line = pending.ToString(0, newline + 1);
                pending.Remove(0, newline + 1);
                if (line.IndexOf("RBDBG|phase=PARSED|", StringComparison.Ordinal) >= 0)
                    return new ApplicationAttachProbeResult("runboard", string.Empty);
                if (global::ProtocolParser.IsRequest(line))
                    return new ApplicationAttachProbeResult("codex-monitor", line);
            }
        }
    }
}
