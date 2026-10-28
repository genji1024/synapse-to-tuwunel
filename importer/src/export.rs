//! Reads the intermediate format described in docs/format.md.

use std::{
	collections::BTreeMap,
	fs::File,
	io::{BufRead, BufReader},
	path::{Path, PathBuf},
};

use serde::{Deserialize, de::DeserializeOwned};
use tuwunel_core::{Err, Result, err};

const FORMAT_VERSION: u32 = 1;

pub(crate) struct Export {
	dir: PathBuf,
	pub(crate) manifest: Manifest,
}

#[derive(Deserialize)]
pub(crate) struct Manifest {
	pub(crate) format_version: u32,
	pub(crate) server_name: String,
	pub(crate) counts: BTreeMap<String, u64>,
}

#[derive(Deserialize)]
pub(crate) struct SigningKey {
	pub(crate) key_id: String,
	pub(crate) seed: Option<String>,
}

#[derive(Deserialize)]
pub(crate) struct User {
	pub(crate) user_id: String,
	pub(crate) deactivated: bool,
	pub(crate) displayname: Option<String>,
	pub(crate) avatar_url: Option<String>,
}

impl Export {
	pub(crate) fn open(dir: &Path) -> Result<Self> {
		let file = File::open(dir.join("manifest.json"))?;
		let manifest: Manifest = serde_json::from_reader(file).map_err(|e| err!("manifest.json: {e}"))?;
		if manifest.format_version != FORMAT_VERSION {
			return Err!("export format {} is not supported", manifest.format_version);
		}

		Ok(Self { dir: dir.to_owned(), manifest })
	}

	pub(crate) fn signing_keys(&self) -> Result<Vec<SigningKey>> { self.lines("signing_keys.jsonl") }

	pub(crate) fn users(&self) -> Result<Vec<User>> { self.lines("users.jsonl") }

	fn lines<T: DeserializeOwned>(&self, name: &str) -> Result<Vec<T>> {
		let file = File::open(self.dir.join(name))?;
		BufReader::new(file)
			.lines()
			.enumerate()
			.map(|(i, line)| {
				serde_json::from_str(&line?).map_err(|e| err!("{name} line {}: {e}", i + 1))
			})
			.collect()
	}
}
