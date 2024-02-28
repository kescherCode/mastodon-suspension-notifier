# Mastodon suspension notifier

This tool reads user connections between your instance and a remote instance you're planning to suspend and
automatically sends each affected local user a DM with all connections that would be severed listed, with a reason and
date of future suspension.

Requires Python 3.11. Lower Python 3 versions may work, but ISO date parsing is incomplete before 3.11.
