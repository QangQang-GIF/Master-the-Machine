# Team orchestrator reference scaffold

This is a reference scaffold for implementing a team pack, not an installable runtime. Empty installer, checker, configuration, skill, and runtime files must be implemented before loading the workspace in MTM Player. Read the accompanying bilingual technical guide.

team-runtime.json matches the current Player manifest validator. Keep exactly its documented fields. package.json, team-runtime.json and team-pack-info.json versions must match. Replace the sample team identity with your registered team identity.

The sample agent profiles illustrate roles; use the provider registration names required by the current Player as documented in the guide.
