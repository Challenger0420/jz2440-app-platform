using System;
using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Jz2440.Control.Core;

namespace Jz2440.Control
{
    public sealed class MainForm : Form
    {
        private readonly ControlViewModel viewModel;
        private readonly ComboBox portCombo = new ComboBox();
        private readonly Label deviceStateLabel = new Label();
        private readonly Label currentAppLabel = new Label();
        private readonly Label currentStateLabel = new Label();
        private readonly Label footerLabel = new Label();
        private readonly FlowLayoutPanel cardsPanel = new FlowLayoutPanel();
        private readonly Dictionary<string, ApplicationCardControl> cards =
            new Dictionary<string, ApplicationCardControl>(StringComparer.Ordinal);
        private readonly CancellationTokenSource lifetime = new CancellationTokenSource();
        private readonly System.Windows.Forms.Timer reconnectTimer = new System.Windows.Forms.Timer();
        private bool connectionAttemptInProgress;

        public MainForm()
        {
            Text = "JZ2440 Control";
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(730, 520);
            MinimumSize = new Size(650, 460);
            BackColor = Color.FromArgb(246, 246, 246);
            Font = new Font("Segoe UI", 9F);

            AppConfiguration configuration = ConfigurationStore.Load();
            reconnectTimer.Interval = Math.Max(1000, configuration.ReconnectMinMs);
            reconnectTimer.Tick += ReconnectTimerOnTick;
            AppRegistry registry = AppRegistry.CreateDefault();
            ILogger logger = new FileLogger();
            string configurationSource = string.Equals(ConfigurationStore.LastLoadedPath,
                ConfigurationStore.GetExecutableSidecarPath(), StringComparison.OrdinalIgnoreCase)
                ? "exe-sidecar"
                : "LocalAppData";
            logger.Info("Configuration loaded from " + configurationSource + "; RunBoardStateScript configured=" +
                (!string.IsNullOrWhiteSpace(configuration.RunBoardStateScript) ? "yes" : "no") + ".");
            if (!string.IsNullOrWhiteSpace(ConfigurationStore.LastLoadDiagnostic))
                logger.Error(ConfigurationStore.LastLoadDiagnostic);
            IDeviceConnection device = string.Equals(configuration.BackendMode, "serial", StringComparison.OrdinalIgnoreCase)
                ? (IDeviceConnection)new SerialDeviceConnection(registry, logger, null, configuration)
                : new MockDeviceConnection(registry, logger);
            viewModel = new ControlViewModel(device, registry, logger);
            viewModel.Changed += ViewModelOnChanged;

            BuildLayout();
            BuildCards(viewModel.Apps);
            UpdateFromSnapshot(viewModel.Snapshot);
            Shown += MainFormOnShown;
            FormClosed += MainFormOnClosed;
        }

        private void BuildLayout()
        {
            TableLayoutPanel root = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                Padding = new Padding(22, 18, 22, 16),
                RowCount = 5,
                ColumnCount = 1,
                BackColor = BackColor
            };
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 48));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 92));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 88));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 28));
            Controls.Add(root);

            Label heading = new Label
            {
                Text = "JZ2440 Control",
                Dock = DockStyle.Fill,
                Font = new Font("Segoe UI", 18F, FontStyle.Bold),
                ForeColor = Color.FromArgb(25, 25, 25),
                TextAlign = ContentAlignment.MiddleLeft
            };
            root.Controls.Add(heading, 0, 0);

            GroupBox deviceGroup = new GroupBox { Text = "Device", Dock = DockStyle.Fill, Padding = new Padding(12, 8, 12, 8) };
            TableLayoutPanel deviceLayout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 6, RowCount = 1 };
            deviceLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 65));
            deviceLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 120));
            deviceLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 45));
            deviceLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            deviceLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 74));
            deviceLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 74));
            deviceGroup.Controls.Add(deviceLayout);

            deviceLayout.Controls.Add(new Label { Text = "Status", Anchor = AnchorStyles.Left, AutoSize = true }, 0, 0);
            deviceStateLabel.AutoSize = true;
            deviceStateLabel.Anchor = AnchorStyles.Left;
            deviceStateLabel.Font = new Font("Segoe UI", 9F, FontStyle.Bold);
            deviceLayout.Controls.Add(deviceStateLabel, 1, 0);
            deviceLayout.Controls.Add(new Label { Text = "Port", Anchor = AnchorStyles.Left, AutoSize = true }, 2, 0);
            portCombo.Dock = DockStyle.Fill;
            portCombo.DropDownStyle = ComboBoxStyle.DropDownList;
            portCombo.Items.Add("Auto discover");
            portCombo.SelectedIndex = 0;
            deviceLayout.Controls.Add(portCombo, 3, 0);
            Button refreshButton = new Button { Text = "Refresh", Dock = DockStyle.Fill };
            refreshButton.Click += RefreshButtonOnClick;
            deviceLayout.Controls.Add(refreshButton, 4, 0);
            Button connectButton = new Button { Text = "Connect", Dock = DockStyle.Fill };
            connectButton.Click += ConnectButtonOnClick;
            deviceLayout.Controls.Add(connectButton, 5, 0);
            root.Controls.Add(deviceGroup, 0, 1);

            GroupBox currentGroup = new GroupBox { Text = "Current App", Dock = DockStyle.Fill, Padding = new Padding(12, 8, 12, 8) };
            TableLayoutPanel currentLayout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1 };
            currentLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            currentLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 120));
            currentGroup.Controls.Add(currentLayout);
            currentAppLabel.Dock = DockStyle.Fill;
            currentAppLabel.Font = new Font("Segoe UI", 12F, FontStyle.Bold);
            currentAppLabel.TextAlign = ContentAlignment.MiddleLeft;
            currentLayout.Controls.Add(currentAppLabel, 0, 0);
            currentStateLabel.Dock = DockStyle.Fill;
            currentStateLabel.Font = new Font("Segoe UI", 10F, FontStyle.Bold);
            currentStateLabel.ForeColor = Color.FromArgb(20, 120, 65);
            currentStateLabel.TextAlign = ContentAlignment.MiddleRight;
            currentLayout.Controls.Add(currentStateLabel, 1, 0);
            root.Controls.Add(currentGroup, 0, 2);

            GroupBox applicationsGroup = new GroupBox { Text = "Applications", Dock = DockStyle.Fill, Padding = new Padding(12, 8, 12, 8) };
            cardsPanel.Dock = DockStyle.Fill;
            cardsPanel.AutoScroll = true;
            cardsPanel.WrapContents = true;
            cardsPanel.FlowDirection = FlowDirection.LeftToRight;
            cardsPanel.BackColor = Color.FromArgb(246, 246, 246);
            applicationsGroup.Controls.Add(cardsPanel);
            root.Controls.Add(applicationsGroup, 0, 3);

            footerLabel.Dock = DockStyle.Fill;
            footerLabel.TextAlign = ContentAlignment.MiddleLeft;
            footerLabel.ForeColor = Color.FromArgb(90, 90, 90);
            root.Controls.Add(footerLabel, 0, 4);
        }

        private void BuildCards(IReadOnlyList<AppCardSnapshot> snapshots)
        {
            cardsPanel.SuspendLayout();
            try
            {
                foreach (AppCardSnapshot snapshot in snapshots)
                {
                    ApplicationCardControl card = new ApplicationCardControl();
                    card.LaunchRequested += CardOnLaunchRequested;
                    cards.Add(snapshot.Id, card);
                    cardsPanel.Controls.Add(card);
                }
            }
            finally
            {
                cardsPanel.ResumeLayout();
            }
        }

        private async void MainFormOnShown(object sender, EventArgs e)
        {
            try
            {
                await ConnectOnceAsync(true);
            }
            catch (OperationCanceledException) { }
            finally
            {
                if (!IsDisposed) reconnectTimer.Start();
            }
        }

        private async void RefreshButtonOnClick(object sender, EventArgs e)
        {
            try
            {
                await RefreshPortsAsync();
            }
            catch (OperationCanceledException) { }
        }

        private async void ConnectButtonOnClick(object sender, EventArgs e)
        {
            try
            {
                await ConnectOnceAsync(false);
            }
            catch (OperationCanceledException) { }
        }

        private async void ReconnectTimerOnTick(object sender, EventArgs e)
        {
            if (viewModel.Snapshot.ConnectionState == DeviceConnectionState.Connected ||
                viewModel.Snapshot.ConnectionState == DeviceConnectionState.Connecting)
                return;
            try
            {
                await ConnectOnceAsync(true);
            }
            catch (OperationCanceledException) { }
        }

        private async Task ConnectOnceAsync(bool rediscover)
        {
            if (connectionAttemptInProgress) return;
            connectionAttemptInProgress = true;
            try
            {
                if (rediscover) await RefreshPortsAsync();
                string selectedPort = GetSelectedPort();
                if (rediscover && string.IsNullOrWhiteSpace(selectedPort))
                {
                    // Do not turn a physically absent device into a visible
                    // Connecting -> Error loop.  The next timer tick will
                    // discover it after Windows enumerates the USB device.
                    footerLabel.Text = "Waiting for compatible serial device.";
                    return;
                }

                await viewModel.ConnectAsync(selectedPort, lifetime.Token);
                if (rediscover && viewModel.Snapshot.ConnectionState == DeviceConnectionState.Error)
                {
                    // A port can reappear before the board is probeable (in
                    // particular while a silent RunBoard target still owns
                    // the UART).  Do not flash the UI forever or send blind
                    // lifecycle commands.  A manual Connect retries safely.
                    reconnectTimer.Stop();
                    footerLabel.Text = viewModel.Snapshot.Message + " Click Connect to retry.";
                }
                else if (viewModel.Snapshot.ConnectionState == DeviceConnectionState.Connected && !IsDisposed)
                {
                    reconnectTimer.Start();
                }
            }
            finally
            {
                connectionAttemptInProgress = false;
            }
        }

        private async Task RefreshPortsAsync()
        {
            IReadOnlyList<string> ports = await viewModel.DiscoverPortsAsync(lifetime.Token);
            portCombo.Items.Clear();
            portCombo.Items.Add("Auto discover");
            foreach (string port in ports) portCombo.Items.Add(port);
            AppConfiguration configuration = ConfigurationStore.Load();
            if (!string.IsNullOrWhiteSpace(configuration.PreferredPort) &&
                portCombo.Items.Contains(configuration.PreferredPort))
                portCombo.SelectedItem = configuration.PreferredPort;
            else
                portCombo.SelectedIndex = ports.Count == 1 ? 1 : 0;
        }

        private async void CardOnLaunchRequested(object sender, EventArgs e)
        {
            ApplicationCardControl card = (ApplicationCardControl)sender;
            SwitchResult result = await viewModel.LaunchAsync(card.AppId, lifetime.Token);
            if (!result.Success)
            {
                footerLabel.Text = result.ErrorCode + ": " + result.Message;
            }
        }

        private string GetSelectedPort()
        {
            string selected = portCombo.SelectedItem as string;
            return string.IsNullOrWhiteSpace(selected) || selected == "Auto discover" || selected == "Mock backend"
                ? null
                : selected;
        }

        private void ViewModelOnChanged(object sender, EventArgs e)
        {
            if (IsDisposed) return;
            if (InvokeRequired)
            {
                BeginInvoke(new Action(() => UpdateFromSnapshot(viewModel.Snapshot)));
                return;
            }
            UpdateFromSnapshot(viewModel.Snapshot);
        }

        private void UpdateFromSnapshot(DeviceSnapshot snapshot)
        {
            deviceStateLabel.Text = snapshot.ConnectionState.ToString();
            deviceStateLabel.ForeColor = snapshot.ConnectionState == DeviceConnectionState.Connected
                ? Color.FromArgb(20, 120, 65)
                : snapshot.ConnectionState == DeviceConnectionState.Error
                    ? Color.FromArgb(180, 55, 35)
                    : Color.FromArgb(95, 95, 95);
            if (snapshot.IsDevelopmentMock && portCombo.Items.Count == 1)
            {
                portCombo.Items[0] = "Mock backend";
                portCombo.SelectedIndex = 0;
            }
            else if (!snapshot.IsDevelopmentMock && !string.IsNullOrEmpty(snapshot.PortName) &&
                     !portCombo.Items.Contains(snapshot.PortName))
            {
                portCombo.Items.Add(snapshot.PortName);
                portCombo.SelectedItem = snapshot.PortName;
            }

            AppCardSnapshot current = snapshot.Apps.FirstOrDefault(item => item.Id == snapshot.CurrentAppId);
            currentAppLabel.Text = current == null ? "Unknown / —" : current.DisplayName;
            currentStateLabel.Text = current == null ? "—" : current.State.ToString();
            currentStateLabel.ForeColor = current == null || current.State != AppState.Running
                ? Color.FromArgb(95, 95, 95)
                : Color.FromArgb(20, 120, 65);
            // A connected UART is not enough to launch an application.  The
            // controller must have confirmed the current board application;
            // otherwise a launch would fail with CURRENT_UNKNOWN and could
            // leave the board-side target running without a host provider.
            bool canLaunchFromSnapshot = snapshot.ConnectionState == DeviceConnectionState.Connected &&
                !string.IsNullOrEmpty(snapshot.CurrentAppId) &&
                !viewModel.IsSwitching;
            foreach (AppCardSnapshot app in snapshot.Apps)
                if (cards.TryGetValue(app.Id, out ApplicationCardControl card))
                    card.UpdateSnapshot(app, canLaunchFromSnapshot);
            footerLabel.Text = snapshot.Message;
        }

        private void MainFormOnClosed(object sender, FormClosedEventArgs e)
        {
            reconnectTimer.Stop();
            reconnectTimer.Dispose();
            lifetime.Cancel();
            viewModel.Dispose();
            lifetime.Dispose();
        }
    }
}
