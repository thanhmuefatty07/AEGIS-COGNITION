/// AEGIS-COGNITION Role-Based Access Control
/// Enterprise-grade RBAC with role hierarchy, permission inheritance, and audit logging
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ─── Permission Model ───

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Permission {
    // ─── Agent Operations ───
    CreateAgent,
    RunAgent,
    ViewAgentResults,
    DeleteAgent,

    // ─── Audit & Evidence ───
    ViewAuditLog,
    ExportComplianceReport,
    VerifyEvidenceChain,

    // ─── Team Management ───
    ViewTeamDashboard,
    ManageTeamMembers,
    InviteTeamMembers,
    RemoveTeamMembers,

    // ─── Skill Management ───
    CreateSkill,
    PublishSkill,
    InstallSkill,
    ManageSkills,

    // ─── Administration ───
    ManageTenants,
    AdministerLicense,
    ViewBillingInfo,
    ManageBilling,

    // ─── System ───
    ViewSystemHealth,
    ConfigureSystem,
    AccessAPI,
}

impl Permission {
    /// Which tier unlocks this permission by default?
    pub fn default_tier(&self) -> Tier {
        match self {
            Permission::CreateAgent | Permission::RunAgent | Permission::ViewAgentResults
            | Permission::DeleteAgent | Permission::InstallSkill | Permission::AccessAPI => Tier::Community,
            Permission::ViewAuditLog | Permission::ViewSystemHealth => Tier::Pro,
            Permission::ViewTeamDashboard | Permission::ManageTeamMembers
            | Permission::InviteTeamMembers | Permission::RemoveTeamMembers
            | Permission::CreateSkill | Permission::ManageSkills => Tier::Team,
            Permission::ExportComplianceReport | Permission::VerifyEvidenceChain
            | Permission::PublishSkill | Permission::ManageTenants
            | Permission::AdministerLicense | Permission::ViewBillingInfo
            | Permission::ManageBilling | Permission::ConfigureSystem => Tier::Enterprise,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Tier {
    Community,
    Pro,
    Team,
    Enterprise,
}

// ─── Role Definitions ───

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Role {
    pub name: String,
    pub description: String,
    pub permissions: Vec<Permission>,
    pub inherits: Vec<String>, // Role names this inherits from
}

impl Role {
    /// Admin role — full access
    pub fn admin() -> Self {
        Self {
            name: "admin".into(),
            description: "Full system access".into(),
            permissions: vec![
                Permission::CreateAgent, Permission::RunAgent, Permission::ViewAgentResults,
                Permission::DeleteAgent, Permission::ViewAuditLog, Permission::ExportComplianceReport,
                Permission::VerifyEvidenceChain, Permission::ViewTeamDashboard, Permission::ManageTeamMembers,
                Permission::InviteTeamMembers, Permission::RemoveTeamMembers, Permission::CreateSkill,
                Permission::PublishSkill, Permission::InstallSkill, Permission::ManageSkills,
                Permission::ManageTenants, Permission::AdministerLicense, Permission::ViewBillingInfo,
                Permission::ManageBilling, Permission::ViewSystemHealth, Permission::ConfigureSystem,
                Permission::AccessAPI,
            ],
            inherits: vec![],
        }
    }

    /// Developer role — agent operations + skill work
    pub fn developer() -> Self {
        Self {
            name: "developer".into(),
            description: "Create and run agents, manage skills".into(),
            permissions: vec![
                Permission::CreateAgent, Permission::RunAgent, Permission::ViewAgentResults,
                Permission::DeleteAgent, Permission::ViewAuditLog, Permission::CreateSkill,
                Permission::InstallSkill, Permission::AccessAPI,
            ],
            inherits: vec![],
        }
    }

    /// Viewer role — read-only access
    pub fn viewer() -> Self {
        Self {
            name: "viewer".into(),
            description: "View results and dashboards".into(),
            permissions: vec![
                Permission::ViewAgentResults, Permission::ViewAuditLog,
                Permission::ViewTeamDashboard, Permission::AccessAPI,
            ],
            inherits: vec![],
        }
    }

    /// Billing admin — financial operations only
    pub fn billing_admin() -> Self {
        Self {
            name: "billing_admin".into(),
            description: "Manage billing and subscriptions".into(),
            permissions: vec![
                Permission::ViewBillingInfo, Permission::ManageBilling,
                Permission::AccessAPI,
            ],
            inherits: vec![],
        }
    }
}

// ─── User & Team ───

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct User {
    pub id: String,
    pub email: String,
    pub name: String,
    pub roles: Vec<String>, // Role names
    pub team_ids: Vec<String>,
    pub created_at: u64,
    pub last_login_at: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Team {
    pub id: String,
    pub name: String,
    pub member_ids: Vec<String>,
    pub default_role: String,
    pub created_at: u64,
}

// ─── RBAC Manager ───

pub struct RBACManager {
    roles: HashMap<String, Role>,
    users: HashMap<String, User>,
    teams: HashMap<String, Team>,
    audit_log: Vec<AccessEvent>,
}

impl RBACManager {
    pub fn new() -> Self {
        let mut roles = HashMap::new();
        for role in [Role::admin(), Role::developer(), Role::viewer(), Role::billing_admin()] {
            roles.insert(role.name.clone(), role);
        }

        Self {
            roles,
            users: HashMap::new(),
            teams: HashMap::new(),
            audit_log: Vec::new(),
        }
    }

    // ─── User Management ───

    pub fn add_user(&mut self, user: User) -> Result<(), RBACError> {
        if self.users.contains_key(&user.id) {
            return Err(RBACError::UserAlreadyExists(user.id));
        }
        self.log(AccessEvent::UserCreated {
            user_id: user.id.clone(),
            email: user.email.clone(),
        });
        self.users.insert(user.id.clone(), user);
        Ok(())
    }

    pub fn assign_role(&mut self, user_id: &str, role_name: &str) -> Result<(), RBACError> {
        if !self.roles.contains_key(role_name) {
            return Err(RBACError::RoleNotFound(role_name.into()));
        }
        let user = self.users.get_mut(user_id).ok_or(RBACError::UserNotFound(user_id.into()))?;
        if !user.roles.contains(&role_name.to_string()) {
            user.roles.push(role_name.to_string());
            self.log(AccessEvent::RoleAssigned {
                user_id: user_id.into(),
                role: role_name.into(),
            });
        }
        Ok(())
    }

    /// Check if a user has a specific permission
    pub fn check_permission(&self, user_id: &str, permission: Permission) -> Result<(), RBACError> {
        let user = self.users.get(user_id).ok_or(RBACError::UserNotFound(user_id.into()))?;

        let mut checked_roles = Vec::new();
        let mut to_check: Vec<String> = user.roles.clone();

        // Resolve role hierarchy
        while let Some(role_name) = to_check.pop() {
            if checked_roles.contains(&role_name) {
                continue;
            }
            checked_roles.push(role_name.clone());

            if let Some(role) = self.roles.get(&role_name) {
                if role.permissions.contains(&permission) {
                    self.log(AccessEvent::PermissionGranted {
                        user_id: user_id.into(),
                        permission,
                        role: role_name,
                    });
                    return Ok(());
                }
                // Check inherited roles
                for inherited in &role.inherits {
                    if !checked_roles.contains(inherited) {
                        to_check.push(inherited.clone());
                    }
                }
            }
        }

        self.log(AccessEvent::PermissionDenied {
            user_id: user_id.into(),
            permission,
        });
        Err(RBACError::PermissionDenied(permission))
    }

    // ─── Team Management ───

    pub fn create_team(&mut self, team: Team) -> Result<(), RBACError> {
        if self.teams.contains_key(&team.id) {
            return Err(RBACError::TeamAlreadyExists(team.id));
        }
        self.teams.insert(team.id.clone(), team);
        Ok(())
    }

    pub fn add_member_to_team(&mut self, team_id: &str, user_id: &str) -> Result<(), RBACError> {
        let user = self.users.get_mut(user_id).ok_or(RBACError::UserNotFound(user_id.into()))?;
        if !user.team_ids.contains(&team_id.to_string()) {
            user.team_ids.push(team_id.to_string());
        }

        let team = self.teams.get(team_id).ok_or(RBACError::TeamNotFound(team_id.into()))?;
        let default_role = team.default_role.clone();
        if !user.roles.contains(&default_role) {
            user.roles.push(default_role);
        }

        Ok(())
    }

    // ─── Audit Log ───

    fn log(&mut self, event: AccessEvent) {
        self.audit_log.push(event);
    }

    pub fn audit_events(&self) -> &[AccessEvent] {
        &self.audit_log
    }

    /// Export RBAC audit trail (for compliance reports)
    pub fn export_audit(&self) -> Vec<AuditEntry> {
        self.audit_log.iter().map(AuditEntry::from_event).collect()
    }
}

// ─── Audit Events ───

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum AccessEvent {
    UserCreated { user_id: String, email: String },
    RoleAssigned { user_id: String, role: String },
    PermissionGranted { user_id: String, permission: Permission, role: String },
    PermissionDenied { user_id: String, permission: Permission },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEntry {
    pub timestamp: u64,
    pub event_type: String,
    pub user_id: String,
    pub detail: String,
}

impl AuditEntry {
    fn from_event(event: &AccessEvent) -> Self {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();

        match event {
            AccessEvent::UserCreated { user_id, email } => AuditEntry {
                timestamp: now,
                event_type: "user_created".into(),
                user_id: user_id.clone(),
                detail: format!("email={}", email),
            },
            AccessEvent::RoleAssigned { user_id, role } => AuditEntry {
                timestamp: now,
                event_type: "role_assigned".into(),
                user_id: user_id.clone(),
                detail: format!("role={}", role),
            },
            AccessEvent::PermissionGranted { user_id, permission, role } => AuditEntry {
                timestamp: now,
                event_type: "permission_granted".into(),
                user_id: user_id.clone(),
                detail: format!("permission={:?} role={}", permission, role),
            },
            AccessEvent::PermissionDenied { user_id, permission } => AuditEntry {
                timestamp: now,
                event_type: "permission_denied".into(),
                user_id: user_id.clone(),
                detail: format!("permission={:?}", permission),
            },
        }
    }
}

// ─── Errors ───

#[derive(Debug)]
pub enum RBACError {
    UserNotFound(String),
    UserAlreadyExists(String),
    RoleNotFound(String),
    TeamNotFound(String),
    TeamAlreadyExists(String),
    PermissionDenied(Permission),
}

impl std::fmt::Display for RBACError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RBACError::UserNotFound(id) => write!(f, "User not found: {}", id),
            RBACError::UserAlreadyExists(id) => write!(f, "User already exists: {}", id),
            RBACError::RoleNotFound(role) => write!(f, "Role not found: {}", role),
            RBACError::TeamNotFound(id) => write!(f, "Team not found: {}", id),
            RBACError::TeamAlreadyExists(id) => write!(f, "Team already exists: {}", id),
            RBACError::PermissionDenied(perm) => write!(f, "Permission denied: {:?}", perm),
        }
    }
}

// ─── Tests ───

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_rbac_basic() {
        let mut rbac = RBACManager::new();

        let alice = User {
            id: "alice-1".into(),
            email: "alice@example.com".into(),
            name: "Alice Admin".into(),
            roles: vec![],
            team_ids: vec![],
            created_at: 1749600000,
            last_login_at: 1749600000,
        };

        rbac.add_user(alice).unwrap();
        rbac.assign_role("alice-1", "admin").unwrap();

        assert!(rbac.check_permission("alice-1", Permission::CreateAgent).is_ok());
        assert!(rbac.check_permission("alice-1", Permission::ManageBilling).is_ok());
        assert!(rbac.check_permission("alice-1", Permission::ConfigureSystem).is_ok());
    }

    #[test]
    fn test_rbac_viewer_restrictions() {
        let mut rbac = RBACManager::new();

        let bob = User {
            id: "bob-1".into(),
            email: "bob@example.com".into(),
            name: "Bob Viewer".into(),
            roles: vec![],
            team_ids: vec![],
            created_at: 1749600000,
            last_login_at: 1749600000,
        };

        rbac.add_user(bob).unwrap();
        rbac.assign_role("bob-1", "viewer").unwrap();

        // Viewer can view
        assert!(rbac.check_permission("bob-1", Permission::ViewAgentResults).is_ok());

        // Viewer cannot create
        assert!(rbac.check_permission("bob-1", Permission::CreateAgent).is_err());

        // Viewer cannot administer
        assert!(rbac.check_permission("bob-1", Permission::AdministerLicense).is_err());
    }

    #[test]
    fn test_rbac_developer_can_create_but_not_administer() {
        let mut rbac = RBACManager::new();

        let dev = User {
            id: "dev-1".into(),
            email: "dev@example.com".into(),
            name: "Dev User".into(),
            roles: vec![],
            team_ids: vec![],
            created_at: 1749600000,
            last_login_at: 1749600000,
        };

        rbac.add_user(dev).unwrap();
        rbac.assign_role("dev-1", "developer").unwrap();

        assert!(rbac.check_permission("dev-1", Permission::CreateAgent).is_ok());
        assert!(rbac.check_permission("dev-1", Permission::RunAgent).is_ok());
        assert!(rbac.check_permission("dev-1", Permission::InstallSkill).is_ok());

        assert!(rbac.check_permission("dev-1", Permission::AdministerLicense).is_err());
        assert!(rbac.check_permission("dev-1", Permission::ManageBilling).is_err());
    }

    #[test]
    fn test_audit_log() {
        let mut rbac = RBACManager::new();

        rbac.add_user(User {
            id: "user-1".into(),
            email: "test@example.com".into(),
            name: "Test".into(),
            roles: vec![],
            team_ids: vec![],
            created_at: 1749600000,
            last_login_at: 1749600000,
        }).unwrap();

        rbac.assign_role("user-1", "admin").unwrap();
        rbac.check_permission("user-1", Permission::CreateAgent).unwrap();
        rbac.check_permission("user-1", Permission::ManageBilling).unwrap();

        let audit = rbac.export_audit();
        assert!(audit.len() >= 4); // user_created + role_assigned + 2x permission_granted
    }
}