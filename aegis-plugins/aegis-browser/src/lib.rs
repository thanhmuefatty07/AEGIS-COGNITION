use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::PathBuf;
use thiserror::Error;
use url::Url;

#[derive(Debug, Error)]
pub enum BrowserError {
    #[error("invalid browser option: {0}")]
    InvalidOption(String),
    #[error("invalid url")]
    InvalidUrl,
    #[error("driver error: {0}")]
    Driver(String),
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BrowserOptions {
    pub headless: bool,
    pub stealth: bool,
    pub proxy: Option<ProxyConfig>,
    pub user_agent: Option<String>,
    pub viewport: Viewport,
    pub cookie_store_path: Option<PathBuf>,
    pub rate_limit_per_minute: u32,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ProxyConfig {
    pub kind: ProxyKind,
    pub endpoint: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum ProxyKind {
    Http,
    Socks5,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct Viewport {
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub enum BrowserCommand {
    Navigate { url: String },
    Click { selector: String },
    TypeText { selector: String, text: String },
    ExtractText { selector: String },
    ExtractLinks,
    Screenshot,
    WaitFor { selector: String, timeout_ms: u64 },
    ExecuteJs { script: String },
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct BrowserEvent {
    pub command_hash: [u8; 32],
    pub output: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct StealthProfile {
    pub mask_webdriver: bool,
    pub mask_plugins: bool,
    pub jitter_viewport: bool,
    pub user_agent: String,
}

pub trait BrowserDriver {
    fn execute(&mut self, command: BrowserCommand) -> Result<BrowserEvent, BrowserError>;
}

pub struct Browser<D> {
    options: BrowserOptions,
    driver: D,
}

pub struct BrowserContext<D> {
    browser: Browser<D>,
    cookies: BTreeMap<String, String>,
}

pub struct Page<'a, D> {
    context: &'a mut BrowserContext<D>,
}

#[derive(Default)]
pub struct MemoryBrowserDriver {
    current_url: Option<String>,
    dom: BTreeMap<String, String>,
}

impl Default for BrowserOptions {
    fn default() -> Self {
        Self {
            headless: true,
            stealth: false,
            proxy: None,
            user_agent: None,
            viewport: Viewport {
                width: 1365,
                height: 768,
            },
            cookie_store_path: None,
            rate_limit_per_minute: 120,
        }
    }
}

impl BrowserOptions {
    pub fn validate(&self) -> Result<(), BrowserError> {
        if self.viewport.width < 320 || self.viewport.height < 240 {
            return Err(BrowserError::InvalidOption(
                "viewport too small".to_string(),
            ));
        }
        if self.rate_limit_per_minute == 0 {
            return Err(BrowserError::InvalidOption(
                "rate limit is zero".to_string(),
            ));
        }
        if let Some(proxy) = &self.proxy {
            Url::parse(&proxy.endpoint)
                .map_err(|_| BrowserError::InvalidOption("invalid proxy endpoint".to_string()))?;
        }
        Ok(())
    }

    pub fn stealth_profile(&self) -> StealthProfile {
        StealthProfile {
            mask_webdriver: self.stealth,
            mask_plugins: self.stealth,
            jitter_viewport: self.stealth,
            user_agent: self
                .user_agent
                .clone()
                .unwrap_or_else(|| "Mozilla/5.0 AEGIS".to_string()),
        }
    }
}

impl BrowserCommand {
    pub fn hash(&self) -> [u8; 32] {
        let payload = serde_json::to_vec(self).unwrap_or_default();
        *blake3::hash(&payload).as_bytes()
    }
}

impl<D: BrowserDriver> Browser<D> {
    pub fn new(options: BrowserOptions, driver: D) -> Result<Self, BrowserError> {
        options.validate()?;
        Ok(Self { options, driver })
    }

    pub fn context(self) -> BrowserContext<D> {
        BrowserContext {
            browser: self,
            cookies: BTreeMap::new(),
        }
    }

    pub fn options(&self) -> &BrowserOptions {
        &self.options
    }
}

impl<D: BrowserDriver> BrowserContext<D> {
    pub fn page(&mut self) -> Page<'_, D> {
        Page { context: self }
    }

    pub fn set_cookie(&mut self, key: impl Into<String>, value: impl Into<String>) {
        self.cookies.insert(key.into(), value.into());
    }
}

impl<D: BrowserDriver> Page<'_, D> {
    pub fn navigate(&mut self, url: &str) -> Result<BrowserEvent, BrowserError> {
        Url::parse(url).map_err(|_| BrowserError::InvalidUrl)?;
        self.context
            .browser
            .driver
            .execute(BrowserCommand::Navigate {
                url: url.to_string(),
            })
    }

    pub fn click(&mut self, selector: &str) -> Result<BrowserEvent, BrowserError> {
        self.context.browser.driver.execute(BrowserCommand::Click {
            selector: selector.to_string(),
        })
    }

    pub fn type_text(&mut self, selector: &str, text: &str) -> Result<BrowserEvent, BrowserError> {
        self.context
            .browser
            .driver
            .execute(BrowserCommand::TypeText {
                selector: selector.to_string(),
                text: text.to_string(),
            })
    }

    pub fn extract_text(&mut self, selector: &str) -> Result<BrowserEvent, BrowserError> {
        self.context
            .browser
            .driver
            .execute(BrowserCommand::ExtractText {
                selector: selector.to_string(),
            })
    }

    pub fn extract_links(&mut self) -> Result<BrowserEvent, BrowserError> {
        self.context
            .browser
            .driver
            .execute(BrowserCommand::ExtractLinks)
    }
}

impl BrowserDriver for MemoryBrowserDriver {
    fn execute(&mut self, command: BrowserCommand) -> Result<BrowserEvent, BrowserError> {
        let output = match &command {
            BrowserCommand::Navigate { url } => {
                self.current_url = Some(url.clone());
                format!("navigated:{url}")
            }
            BrowserCommand::Click { selector } => format!("clicked:{selector}"),
            BrowserCommand::TypeText { selector, text } => {
                self.dom.insert(selector.clone(), text.clone());
                format!("typed:{selector}")
            }
            BrowserCommand::ExtractText { selector } => {
                self.dom.get(selector).cloned().unwrap_or_default()
            }
            BrowserCommand::ExtractLinks => self.current_url.clone().unwrap_or_default(),
            BrowserCommand::Screenshot => "screenshot:memory".to_string(),
            BrowserCommand::WaitFor { selector, .. } => format!("waited:{selector}"),
            BrowserCommand::ExecuteJs { script } => format!("js:{}", script.len()),
        };
        Ok(BrowserEvent {
            command_hash: command.hash(),
            output,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn memory_browser_navigates_and_extracts_typed_text() {
        let browser = Browser::new(
            BrowserOptions {
                stealth: true,
                ..BrowserOptions::default()
            },
            MemoryBrowserDriver::default(),
        )
        .unwrap();
        assert!(browser.options().stealth_profile().mask_webdriver);
        let mut context = browser.context();
        let mut page = context.page();
        page.navigate("https://example.com").unwrap();
        page.type_text("#q", "aegis").unwrap();
        let event = page.extract_text("#q").unwrap();
        assert_eq!(event.output, "aegis");
        assert!(event.command_hash.iter().any(|byte| *byte != 0));
    }
}
