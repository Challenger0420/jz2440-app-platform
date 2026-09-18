using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.Serialization.Json;
using System.Text;

public static class ProviderTests
{
    public static int Run()
    {
        string samplePath = Path.Combine("apps", "codex-monitor", "host", "testdata", "rate_limits_sample.json");
        RpcRateLimitsResponse sample;
        using (MemoryStream stream = new MemoryStream(Encoding.UTF8.GetBytes(File.ReadAllText(samplePath))))
            sample = (RpcRateLimitsResponse)new DataContractJsonSerializer(typeof(RpcRateLimitsResponse)).ReadObject(stream);
        if (sample == null || sample.Result == null || sample.Result.RateLimits == null ||
            sample.Result.RateLimits.Primary == null || sample.Result.RateLimits.Primary.UsedPercent != 22 ||
            sample.Result.ResetCredits == null || sample.Result.ResetCredits.AvailableCount != 3)
            throw new InvalidOperationException("saved app-server sample response mismatch");

        QuotaSnapshot expected = new QuotaSnapshot
        {
            FiveHourRemaining = 78,
            FiveHourReset = 1788966311,
            WeeklyRemaining = 87,
            WeeklyReset = 1789448757,
            ResetCards = 3,
            Now = 1788958655,
            TimeZoneOffsetMinutes = 480
        };
        using (IQuotaProvider provider = new MockCodexProvider(expected))
        {
            QuotaSnapshot actual = provider.ReadQuota();
            if (actual.FiveHourRemaining != 78 || actual.WeeklyRemaining != 87 ||
                actual.ResetCards != 3 || actual.TimeZoneOffsetMinutes != 480)
                throw new InvalidOperationException("mock provider snapshot mismatch");
            string frame = ProtocolEncoder.Encode(actual);
            Dictionary<string, string> fields;
            if (!ProtocolParser.TryParse(frame, out fields))
                throw new InvalidOperationException("mock provider frame was not parseable");
        }

        string tempRoot = Path.Combine(Path.GetTempPath(), "jz2440-codex-provider-test-" + Guid.NewGuid().ToString("N"));
        string installed = Path.Combine(tempRoot, "OpenAI", "Codex", "bin", "test-version", "codex.exe");
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(installed));
            File.WriteAllText(installed, "test");
            string resolved = RealCodexProvider.ResolveCodexExecutable(null, String.Empty, tempRoot);
            if (!String.Equals(Path.GetFullPath(installed), resolved, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("user-local Codex installation fallback mismatch");
        }
        finally
        {
            try { if (Directory.Exists(tempRoot)) Directory.Delete(tempRoot, true); } catch { }
        }
        Console.WriteLine("provider/encoder self-test: PASS");
        return 0;
    }
}
