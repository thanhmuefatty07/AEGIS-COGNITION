//! Concrete bounded execution lanes.
//!
//! Admission and execution are separate concerns, but the executor still
//! acquires the same lane before invoking work. This prevents a caller from
//! bypassing the registry by calling a worker directly.

use crate::resource::{ExecutionLane, ExecutionLaneRegistry, HardwareProfile, ResourceError};
use rayon::ThreadPool;
use std::future::Future;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::sync::{Arc, Mutex};

#[derive(Debug)]
pub struct ExecutionLanes {
    registry: Arc<Mutex<ExecutionLaneRegistry>>,
    cpu_pool: ThreadPool,
    io_runtime: Mutex<tokio::runtime::Runtime>,
}

impl ExecutionLanes {
    pub fn new(profile: &HardwareProfile) -> Result<Self, ResourceError> {
        let registry = ExecutionLaneRegistry::for_profile(profile);
        let cpu_threads = registry
            .lanes
            .get(&ExecutionLane::Cpu)
            .map(|limit| limit.max_in_flight)
            .unwrap_or(1)
            .max(1);
        let cpu_pool = rayon::ThreadPoolBuilder::new()
            .num_threads(cpu_threads as usize)
            .thread_name(|index| format!("aegis-cpu-{index}"))
            .build()
            .map_err(|error| {
                ResourceError::InvalidRequest(format!("CPU lane build failed: {error}"))
            })?;
        let io_runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .map_err(|error| {
                ResourceError::InvalidRequest(format!("I/O lane build failed: {error}"))
            })?;
        Ok(Self {
            registry: Arc::new(Mutex::new(registry)),
            cpu_pool,
            io_runtime: Mutex::new(io_runtime),
        })
    }

    pub fn snapshot(&self) -> ExecutionLaneRegistry {
        self.registry
            .lock()
            .expect("execution lane mutex poisoned")
            .clone()
    }

    pub fn run_cpu<F, R>(&self, work: F) -> Result<R, ResourceError>
    where
        F: FnOnce() -> R + Send,
        R: Send,
    {
        Ok(self.with_lane(ExecutionLane::Cpu, || {
            catch_unwind(AssertUnwindSafe(|| self.cpu_pool.install(work)))
                .map_err(|_| ResourceError::InvalidRequest("CPU lane work panicked".to_string()))
        })?)
    }

    pub fn run_python_blocking<F, R>(&self, work: F) -> Result<R, ResourceError>
    where
        F: FnOnce() -> R + Send,
        R: Send,
    {
        Ok(self.with_lane(ExecutionLane::PythonCognition, || {
            catch_unwind(AssertUnwindSafe(work))
                .map_err(|_| ResourceError::InvalidRequest("Python lane work panicked".to_string()))
        })?)
    }

    pub fn run_untrusted<F, R>(&self, work: F) -> Result<R, ResourceError>
    where
        F: FnOnce() -> R + Send,
        R: Send,
    {
        Ok(self.with_lane(ExecutionLane::Untrusted, || {
            catch_unwind(AssertUnwindSafe(work)).map_err(|_| {
                ResourceError::InvalidRequest("untrusted lane work panicked".to_string())
            })
        })?)
    }

    /// Execute an accelerator-capable work unit only when the probed profile
    /// advertised accelerator capacity. Vendor-specific execution remains an
    /// adapter concern; this lane is the bounded admission seam.
    pub fn run_accelerator<F, R>(&self, work: F) -> Result<R, ResourceError>
    where
        F: FnOnce() -> R + Send,
        R: Send,
    {
        Ok(self.with_lane(ExecutionLane::Accelerator, || {
            catch_unwind(AssertUnwindSafe(work)).map_err(|_| {
                ResourceError::InvalidRequest("accelerator lane work panicked".to_string())
            })
        })?)
    }

    pub fn run_io<F, R>(&self, future: F) -> Result<R, ResourceError>
    where
        F: Future<Output = R>,
    {
        Ok(self.with_lane(ExecutionLane::Io, || {
            let runtime = self.io_runtime.lock().map_err(|_| {
                ResourceError::InvalidRequest("I/O runtime mutex poisoned".to_string())
            })?;
            catch_unwind(AssertUnwindSafe(|| runtime.block_on(future)))
                .map_err(|_| ResourceError::InvalidRequest("I/O lane work panicked".to_string()))
        })?)
    }

    fn with_lane<F, R>(&self, lane: ExecutionLane, work: F) -> Result<R, ResourceError>
    where
        F: FnOnce() -> Result<R, ResourceError>,
    {
        {
            let mut registry = self.registry.lock().map_err(|_| {
                ResourceError::InvalidRequest("execution lane mutex poisoned".to_string())
            })?;
            if !registry.try_acquire(lane) {
                return Err(ResourceError::ResourceExhausted);
            }
        }
        let result = work();
        let released = self
            .registry
            .lock()
            .map_err(|_| {
                ResourceError::InvalidRequest("execution lane mutex poisoned".to_string())
            })?
            .release(lane);
        if !released {
            return Err(ResourceError::InvalidRequest(
                "execution lane release mismatch".to_string(),
            ));
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cpu_and_python_lanes_execute_with_bounded_accounting() {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).unwrap();
        assert_eq!(lanes.run_cpu(|| 2 + 2).unwrap(), 4);
        assert_eq!(lanes.run_python_blocking(|| "ok").unwrap(), "ok");
        assert_eq!(lanes.snapshot().lanes[&ExecutionLane::Cpu].active, 0);
        assert_eq!(
            lanes.snapshot().lanes[&ExecutionLane::PythonCognition].active,
            0
        );
    }

    #[test]
    fn io_lane_runs_future_without_leaking_capacity() {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).unwrap();
        assert_eq!(lanes.run_io(async { 7_u8 }).unwrap(), 7);
        assert_eq!(lanes.snapshot().lanes[&ExecutionLane::Io].active, 0);
    }

    #[test]
    fn accelerator_lane_is_optional_and_fails_closed_without_a_device() {
        let profile = HardwareProfile::probe();
        let lanes = ExecutionLanes::new(&profile).unwrap();
        assert_eq!(
            lanes.run_accelerator(|| 7),
            Err(ResourceError::ResourceExhausted)
        );
    }
}
