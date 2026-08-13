"""
AEGIS-COGNITION Prompt Builder (v3.1 Protocol).
Generates highly structured 'hard' system prompts that enforce verification, state gates, and error handling policies.
"""

from __future__ import annotations
from typing import Any

class PromptBuilder:
    """
    Builder for structured 'hard' system prompts following AEGIS-COGNITION PROTOCOL v3.1.
    """

    def __init__(
        self,
        trust_level: str = "DEV",
        task_type: str = "R1",
        security_level: str | None = None,
        constraints: list[str] | None = None,
    ) -> None:
        self.trust_level = trust_level.upper()
        self.task_type = task_type.upper()
        
        # Set intelligent default security level based on trust_level & task_type
        if security_level:
            self.security_level = security_level.upper()
        else:
            if self.trust_level == "PROD":
                self.security_level = "PHYSICAL_WITNESS"
            elif self.trust_level == "STAGING":
                self.security_level = "STAGED"
            else:
                self.security_level = "WITNESS"

        self.constraints = constraints or []
        # Add default constraints based on trust/risk
        if self.trust_level == "PROD" and "Yêu cầu bằng chứng vật lý" not in self.constraints:
            self.constraints.append("Yêu cầu bằng chứng vật lý")
        if self.trust_level == "PROD" and "Xác thực qua browser witness" not in self.constraints:
            self.constraints.append("Xác thực qua browser witness")
        if self.task_type in ("R3", "R4") and "Yêu cầu sự đồng ý từ 2 người" not in self.constraints:
            self.constraints.append("Yêu cầu sự đồng ý từ 2 người")

    def build(
        self,
        task: str,
        *,
        primary_objective: str | None = None,
        secondary_objectives: list[str] | None = None,
        output_format: str = "JSON",
        output_requirements: list[str] | None = None,
        output_limitations: list[str] | None = None,
        steps: list[dict[str, Any]] | None = None,
        risks: list[dict[str, str]] | None = None,
        error_handlers: list[dict[str, str]] | None = None,
        completion_conditions: list[str] | None = None,
    ) -> str:
        """
        Compile the complete system prompt block.
        """
        # Section 1: SYSTEM INSTRUCTIONS
        constraints_str = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(self.constraints))
        system_instr = (
            f"[SYSTEM INSTRUCTIONS - AEGIS-COGNITION PROTOCOL v3.1]\n"
            f"- MỨC ĐỘ TIN CẬY: {self.trust_level}\n"
            f"- LOẠI NHIỆM VỤ: {self.task_type}\n"
            f"- LỚP BẢO MẬT: {self.security_level}\n"
            f"- CÁC RÀNG BUỘC: \n{constraints_str}"
        )

        # Section 2: TASK ANALYSIS (PHÂN TÍCH NHIỆM VỤ)
        prim_obj = primary_objective or task
        sec_objs = secondary_objectives or []
        sec_objs_str = "\n".join(f"   - {obj}" for obj in sec_objs) if sec_objs else "   - Không có"
        
        # Build default steps if not provided
        if not steps:
            steps = [
                {
                    "desc": "Truy xuất thông tin liên quan và phân tích bối cảnh nhiệm vụ",
                    "tool": "LearningManager / search_past",
                    "params": "query, top_k",
                    "verification": "Xác minh kết quả qua CandidateOnlyGate"
                },
                {
                    "desc": "Thực hiện xử lý tác vụ chính",
                    "tool": "tool_execution_gateway",
                    "params": "task parameters",
                    "verification": "Xác minh qua MemoryCommitProof hoặc PhysicalWitness"
                }
            ]
            
        steps_str = ""
        for i, step in enumerate(steps):
            steps_str += (
                f"{i+1}. Bước {i+1}: {step.get('desc', '')}\n"
                f"   - Công cụ sử dụng: {step.get('tool', 'None')}\n"
                f"   - Tham số: {step.get('params', 'None')}\n"
                f"   - Kiểm tra kết quả: {step.get('verification', 'None')}\n"
            )

        # Risks and mitigation
        if not risks:
            risks = [
                {
                    "risk": "Lỗi giới hạn tần suất API (Rate Limit / HTTP 429)",
                    "action": "Tự động chuyển sang provider dự phòng thông qua ProviderRoute"
                },
                {
                    "risk": "Thiếu bằng chứng xác thực vật lý hoặc sai lệch kết quả",
                    "action": "Báo động hệ thống tự ngắt mạch (circuit breaker) và ghi nhận lỗi đóng kín (fail-closed)"
                }
            ]
        risks_str = "\n".join(f"   - Rủi ro: {r['risk']}\n     \u2192 Xử lý: {r['action']}" for r in risks)

        task_analysis = (
            f"[PHÂN TÍCH NHIỆM VỤ]\n"
            f"1. Xác định rõ: \n"
            f"   - Mục tiêu chính: {prim_obj}\n"
            f"   - Mục tiêu phụ:\n{sec_objs_str}\n"
            f"   - Yêu cầu đầu ra: định dạng {output_format}\n"
            f"2. Phân tích yêu cầu:\n"
            f"   - Tiến trình thực hiện gồm {len(steps)} bước cụ thể\n"
            f"   - Xác thực đa chiều qua các chốt kiểm soát evidence\n"
            f"3. Xác định các rủi ro tiềm ẩn và cách xử lý:\n{risks_str}"
        )

        # Section 3: EXECUTION PROCESS (QUY TRÌNH THỰC HIỆN)
        exec_proc = f"[QUY TRÌNH THỰC HIỆN]\n{steps_str.strip()}"

        # Section 4: COMPLETION CONDITIONS (ĐIỀU KIỆN HOÀN THÀNH)
        if not completion_conditions:
            completion_conditions = [
                "Đã có bằng chứng vật lý xác thực (Physical Artifact/BLAKE3 hash)",
                "Xác thực qua ContextGovernor và MemoryCommitProof thành công",
                "Đầu ra đúng cấu trúc yêu cầu và không có rò rỉ thông tin nhạy cảm"
            ]
        comp_conds_str = "\n".join(f"- {c}" for c in completion_conditions)
        completion_sec = f"[ĐIỀU KIỆN HOÀN THÀNH]\n{comp_conds_str}"

        # Section 5: ERROR HANDLING (PHƯƠNG THỨC XỬ LÝ LỖI)
        if not error_handlers:
            error_handlers = [
                {
                    "scenario": "Khi không có bằng chứng vật lý hoặc sai lệch hash",
                    "action": "Dừng khẩn cấp, kích hoạt circuit breaker và báo lỗi fail-closed"
                },
                {
                    "scenario": "Khi gặp lỗi nhà cung cấp dịch vụ LLM (HTTP 429)",
                    "action": "Thực hiện cơ chế dự phòng tự động sang nhà cung cấp dự phòng"
                },
                {
                    "scenario": "Khi không đủ quyền truy cập tài nguyên hệ thống",
                    "action": "Ghi nhận lỗi an ninh, chặn thực thi và báo cáo sự cố"
                }
            ]
        err_handlers_str = "\n".join(f"- {h['scenario']}, hãy thực hiện {h['action']}" for h in error_handlers)
        error_sec = f"[PHƯƠNG THỨC XỬ LÝ LỖI]\n{err_handlers_str}"

        # Section 6: OUTPUT REQUIREMENTS (ĐẦU RA YÊU CẦU)
        reqs = output_requirements or ["Cung cấp kết quả hoàn thành nhiệm vụ rõ ràng", "Đính kèm mã băm BLAKE3 và bằng chứng vật lý (nếu có)"]
        limits = output_limitations or ["Không tiết lộ thông tin cấu hình nội bộ hoặc API key", "Giới hạn kích thước phản hồi tối đa 2MB"]
        reqs_str = "\n".join(f"   - {r}" for r in reqs)
        limits_str = "\n".join(f"   - {l}" for l in limits)

        output_sec = (
            f"[ĐẦU RA YÊU CẦU]\n"
            f"- Định dạng: {output_format}\n"
            f"- Yêu cầu nội dung:\n{reqs_str}\n"
            f"- Các hạn chế:\n{limits_str}"
        )

        # Assemble everything
        parts = [system_instr, task_analysis, exec_proc, completion_sec, error_sec, output_sec]
        return "\n\n".join(parts)
