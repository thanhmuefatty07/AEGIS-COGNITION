use aegis_nerve::resource::{
    CpuArchitecture, EnforcementLevel, HardwareProfile, MemoryDomain, MemoryDomainKind,
    OperatingProfile, RESOURCE_CONTRACT_SCHEMA_V1, ResourceControlCapabilities, ResourcePolicy,
};

fn fixture(threads: usize, memory_bytes: u64, backend: &str) -> HardwareProfile {
    let mut profile = HardwareProfile::probe();
    profile.cpu.architecture = CpuArchitecture::current();
    profile.cpu.usable_parallelism = threads;
    profile.cpu.logical_processors = Some(threads);
    profile.cpu.numa_nodes[0].usable_parallelism = threads;
    profile.memory_domains = vec![MemoryDomain {
        id: "host".to_string(),
        kind: MemoryDomainKind::Host,
        capacity_bytes: Some(memory_bytes),
        available_bytes: Some(memory_bytes / 2),
        reserved_bytes: 0,
    }];
    profile.os = ResourceControlCapabilities {
        cpu: EnforcementLevel::MeasurementOnly,
        memory: EnforcementLevel::MeasurementOnly,
        process_count: EnforcementLevel::MeasurementOnly,
        thread_count: EnforcementLevel::MeasurementOnly,
        io: EnforcementLevel::MeasurementOnly,
        termination: EnforcementLevel::BestEffort,
        backend: backend.to_string(),
    };
    profile.policy = ResourcePolicy::default();
    profile
}

#[test]
fn weak_normal_and_workstation_profiles_select_deterministic_modes() {
    let fixtures = [
        (
            fixture(2, 4 * 1024 * 1024 * 1024, "linux-cgroup-v2"),
            OperatingProfile::Constrained,
        ),
        (
            fixture(8, 16 * 1024 * 1024 * 1024, "windows-job-object"),
            OperatingProfile::BalancedLaptop,
        ),
        (
            fixture(32, 128 * 1024 * 1024 * 1024, "macos-cooperative"),
            OperatingProfile::Performance,
        ),
    ];
    for (profile, expected) in fixtures {
        assert_eq!(profile.schema, RESOURCE_CONTRACT_SCHEMA_V1);
        assert_eq!(profile.operating_profile(), expected);
        let encoded = serde_json::to_string(&profile).unwrap();
        let decoded: HardwareProfile = serde_json::from_str(&encoded).unwrap();
        assert_eq!(decoded, profile);
        let admission = aegis_nerve::resource::AdmissionController::from_hardware(&profile);
        assert!(admission.capacity().host_memory_bytes > 0);
    }
}

#[test]
fn platform_capability_fixtures_preserve_honest_enforcement_levels() {
    for backend in ["linux-cgroup-v2", "windows-job-object", "macos-cooperative"] {
        let profile = fixture(8, 16 * 1024 * 1024 * 1024, backend);
        assert_eq!(profile.os.backend, backend);
        assert_eq!(profile.os.memory, EnforcementLevel::MeasurementOnly);
        assert!(matches!(
            profile.os.termination,
            EnforcementLevel::BestEffort | EnforcementLevel::MeasurementOnly
        ));
    }
}
