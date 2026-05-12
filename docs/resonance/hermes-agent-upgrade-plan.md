# Hermes Agent Upgrade Plan

## Goal

Hermes를 사용해서 개발용 에이전트를 먼저 고도화하고, 최종적으로는 Resonance/Carbonet 프레임워크가 Hermes 없이도 Ollama 기반으로 동작 가능한 운영 플랫폼이 되도록 한다.

즉 역할을 분리한다.

- `Hermes`: 개발용 에이전트 제작, 오케스트레이션 실험, skill 실험, 평가 자동화
- `Carbonet/Resonance`: 운영 제품, Ollama + custom control plane 기반 실행

## Key Answer

### Can Hermes-developed agents replace Codex?

부분적으로 가능하다.

정확한 해석:

- Hermes는 Codex를 완전히 대체하는 모델이 아니다
- Hermes는 에이전트 프레임워크/오케스트레이션 계층이다
- 따라서 Codex 대신 Ollama 모델, Gemini, Cerebras 등을 Hermes에 연결해서 개발용 에이전트를 만들 수 있다

즉:

- `Codex`는 구현/패치 성능이 강한 모델/도구
- `Hermes`는 모델을 조합하고 workflow를 만들기 위한 시스템

따라서 Hermes를 사용해 개발용 에이전트를 만들고, 그 결과물을 나중에 `Hermes 비의존 운영 플랫폼`으로 이식하는 전략이 맞다.

## Recommended Split

### 1. Hermes side

Hermes에는 아래를 둔다.

- model routing experiments
- skill orchestration
- memory/index experiments
- route/capability resolver experiments
- evaluation harness
- codex/ollama/gemini/cerebras 비교 테스트
- codex / hermes-codex-cerebras runner definitions
- docs/skills sync reference for Carbonet install governance

### 2. Carbonet / Resonance side

Carbonet/Resonance에는 아래를 둔다.

- project/common/ops/builder/theme contracts
- runtime manager
- migration manager
- backup/rollback workers
- ollama model gateway
- approval/safety gates
- manifest registry

## Hermes Work Stages

### Stage 1. Resolver agent

목표:

- 요청을 ops/common/project/builder/theme/db로 분류
- routeId, family, featureEntry, capability를 확정

추천 모델:

- `qwen2.5-coder:3b`
- `gemma3:4b`

### Stage 2. Planner agent

목표:

- candidate files 범위를 좁힌 상태에서 작업 계획 수립

추천 모델:

- `qwen2.5-coder:14b`
- `devstral`
- `gemini-2.5-pro` fallback

### Stage 3. Implementer agent

목표:

- bounded file set 내 실제 수정
- patch / changed_files / artifact 반환

추천 모델:

- `qwen2.5-coder:14b`
- 어려운 경우 `codex` 또는 `cerebras-235b`

### Stage 4. Verifier agent

목표:

- changed_files, artifacts, boundary, contract 위반 판정

추천 모델:

- `qwen2.5-coder:3b`
- 규칙 기반 verifier 병행

## Hermes Output Goal

Hermes의 최종 산출물은 운영 제품이 아니다.

최종 산출물:

- prompt contracts
- JSON handoff contracts
- route resolver logic
- candidate selection logic
- evaluation scripts
- runtime worker requirements

즉 Hermes는 운영 플랫폼의 `개발 공장` 역할을 한다.

## What should not remain Hermes-dependent

운영 제품이 아래에 의존하면 안 된다.

- Hermes memory format
- Hermes runtime
- Hermes-specific prompt loader
- Hermes-only orchestration APIs

운영 제품은 아래만 남겨야 한다.

- Ollama
- model manifest
- control plane
- workers
- registry
- approval gates

추가로 Hermes에서만 유지하고 운영 runtime에는 직접 넣지 않는 것:

- Harness benchmark flow
- Unsloth / Axolotl finetune experiments
- Codex comparative routing prompts

## Migration Path

1. Hermes에서 에이전트 실험
2. 성공 패턴을 control plane spec으로 추출
3. Carbonet/Resonance에 worker + gateway + registry로 구현
4. 운영 플랫폼은 Hermes 없이 배포
