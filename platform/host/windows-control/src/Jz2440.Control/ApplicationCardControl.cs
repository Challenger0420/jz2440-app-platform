using System;
using System.Drawing;
using System.Windows.Forms;
using Jz2440.Control.Core;

namespace Jz2440.Control
{
    public sealed class ApplicationCardControl : UserControl
    {
        private readonly Label titleLabel = new Label();
        private readonly Label stateLabel = new Label();
        private readonly Button actionButton = new Button();

        public ApplicationCardControl()
        {
            Width = 205;
            Height = 128;
            Margin = new Padding(0, 0, 12, 12);
            Padding = new Padding(14);
            BorderStyle = BorderStyle.FixedSingle;
            BackColor = Color.White;

            titleLabel.AutoSize = false;
            titleLabel.Dock = DockStyle.Top;
            titleLabel.Height = 28;
            titleLabel.Font = new Font("Segoe UI", 10F, FontStyle.Bold);
            titleLabel.ForeColor = Color.FromArgb(30, 30, 30);

            stateLabel.AutoSize = false;
            stateLabel.Dock = DockStyle.Top;
            stateLabel.Height = 25;
            stateLabel.Font = new Font("Segoe UI", 9F, FontStyle.Regular);

            actionButton.Dock = DockStyle.Bottom;
            actionButton.Height = 30;
            actionButton.FlatStyle = FlatStyle.System;
            actionButton.Click += ActionButtonOnClick;

            Controls.Add(actionButton);
            Controls.Add(stateLabel);
            Controls.Add(titleLabel);
        }

        public string AppId { get; private set; }
        public event EventHandler LaunchRequested;

        public void UpdateSnapshot(AppCardSnapshot snapshot, bool canInteract = true)
        {
            AppId = snapshot.Id;
            titleLabel.Text = snapshot.DisplayName;
            stateLabel.Text = snapshot.State.ToString();
            stateLabel.ForeColor = snapshot.State == AppState.Running
                ? Color.FromArgb(20, 120, 65)
                : snapshot.State == AppState.Failed
                    ? Color.FromArgb(180, 55, 35)
                    : Color.FromArgb(95, 95, 95);

            if (snapshot.IsCurrent || snapshot.State == AppState.Running)
            {
                actionButton.Text = "Running";
                actionButton.Enabled = false;
                BackColor = Color.FromArgb(237, 248, 241);
            }
            else if (snapshot.State == AppState.Starting || snapshot.State == AppState.Stopping)
            {
                actionButton.Text = snapshot.State.ToString();
                actionButton.Enabled = false;
                BackColor = Color.FromArgb(248, 248, 248);
            }
            else if (snapshot.State == AppState.Unavailable || !snapshot.CanLaunch)
            {
                actionButton.Text = "Unavailable";
                actionButton.Enabled = false;
                BackColor = Color.FromArgb(248, 248, 248);
            }
            else if (snapshot.State == AppState.Failed)
            {
                actionButton.Text = "Retry";
                actionButton.Enabled = canInteract;
                BackColor = Color.FromArgb(255, 248, 242);
            }
            else
            {
                actionButton.Text = "Launch";
                actionButton.Enabled = canInteract;
                BackColor = Color.White;
            }
        }

        private void ActionButtonOnClick(object sender, EventArgs e)
        {
            LaunchRequested?.Invoke(this, EventArgs.Empty);
        }
    }
}
