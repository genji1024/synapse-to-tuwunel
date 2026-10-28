//! Writes an export made by `exporter/export.py` into a Tuwunel database.
//!
//! Run it twice against an empty database, with Tuwunel itself stopped:
//!
//!   synapse-to-tuwunel keypair tuwunel.toml EXPORT_DIR
//!   synapse-to-tuwunel import  tuwunel.toml EXPORT_DIR PASSWORDS_OUT
//!
//! The first run stores Synapse's signing key. It is a separate process
//! because Tuwunel reads its key once, when its services are built, and the
//! second run's first-start setup must already sign with the migrated key.

mod export;

use std::{env, fs, io::Write, path::Path, sync::Arc};

use base64::{Engine, engine::general_purpose::STANDARD_NO_PAD};
use tokio::runtime::Handle;
use tracing::subscriber::NoSubscriber;
use tuwunel_core::{
	Err, Result, Server,
	config::{Config, Sources},
	err,
	log::{LogLevelReloadHandles, Logging, capture::State},
	metrics::Metrics,
	ruma::{OwnedMxcUri, OwnedUserId},
	utils::random_string,
};
use tuwunel_service::Services;

use crate::export::Export;

#[tokio::main]
async fn main() -> Result {
	let args: Vec<String> = env::args().skip(1).collect();
	match args.iter().map(String::as_str).collect::<Vec<_>>()[..] {
		| ["keypair", config, export] => keypair(config, Path::new(export)).await,
		| ["import", config, export, passwords] =>
			import(config, Path::new(export), Path::new(passwords)).await,
		| _ => Err!("usage: keypair CONFIG EXPORT | import CONFIG EXPORT PASSWORDS_OUT"),
	}
}

/// Builds Tuwunel's services on the configured database without listening.
async fn services(config_path: &str) -> Result<Arc<Services>> {
	let raw = Config::load([Path::new(config_path)].into_iter())?;
	let config = Config::new(&raw)?;
	let runtime = Handle::current();
	let logging = Logging {
		subscriber: Arc::new(NoSubscriber::new()),
		reload: LogLevelReloadHandles::default(),
		capture: Arc::new(State::new()),
	};
	let metrics = Metrics::new(Some(&runtime));
	let server = Arc::new(Server::new(config, Sources::default(), Some(&runtime), logging, metrics));

	Services::build(server).await
}

async fn keypair(config: &str, dir: &Path) -> Result {
	let export = Export::open(dir)?;
	let services = services(config).await?;
	if services.server.name.as_str() != export.manifest.server_name {
		return Err!("server_name must be {:?}, as on Synapse", export.manifest.server_name);
	}

	let key = export
		.signing_keys()?
		.into_iter()
		.find_map(|k| Some((k.key_id, k.seed?)))
		.ok_or_else(|| err!("the export has no current signing key"))?;
	let version = key
		.0
		.strip_prefix("ed25519:")
		.ok_or_else(|| err!("unsupported key {}", key.0))?
		.to_owned();
	let seed = STANDARD_NO_PAD
		.decode(key.1.trim_end_matches('='))
		.map_err(|e| err!("signing key seed: {e}"))?;
	if seed.len() != 32 {
		return Err!("signing key seed has {} bytes, not 32", seed.len());
	}

	// PKCS#8 v1 for an Ed25519 seed (RFC 8410), the form Tuwunel loads.
	let mut der = vec![
		0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04,
		0x20,
	];
	der.extend_from_slice(&seed);
	services.db["global"].raw_put(b"keypair", &(version.clone(), der));
	println!("stored signing key ed25519:{version}");
	Ok(())
}

async fn import(config: &str, dir: &Path, passwords_out: &Path) -> Result {
	let export = Export::open(dir)?;
	let services = services(config).await?.start().await?;
	let mut passwords = fs::File::create(passwords_out)?;

	for user in export.users()? {
		let user_id = OwnedUserId::try_from(user.user_id.as_str())?;
		if user.deactivated {
			services.users.create(&user_id, None, None).await?;
			continue;
		}

		// Tuwunel only verifies argon2 hashes, so Synapse's bcrypt hash cannot
		// be kept; each user gets a new password to hand out.
		let password = random_string(24);
		services.users.create(&user_id, Some(&password), None).await?;
		writeln!(passwords, "{user_id}\t{password}")?;

		if let Some(name) = &user.displayname {
			services.profile.set_displayname(&user_id, Some(name), None).await?;
		}
		if let Some(url) = &user.avatar_url {
			let url = OwnedMxcUri::from(url.as_str());
			services.profile.set_avatar_url(&user_id, Some(&url), None).await?;
		}
	}
	println!("imported {} users", export.manifest.counts.get("users.jsonl").unwrap_or(&0));

	// The services' workers run until the server stops, as when Tuwunel itself
	// shuts down.
	services.server.shutdown()?;
	services.stop().await;
	Ok(())
}
