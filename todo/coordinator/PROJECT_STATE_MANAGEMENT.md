# Project Lifecycle State Model (Initial Version)

## Overview

Implement a finite state machine for projects with the following goals:

- Start from user intent and natural-language description.
- Allow model creation/refinement and verification by specialized agents.
- Only allow implementation, testing, and deployment after passing gates.
- Allow description revision at any stage, with controlled consequences.

## States

Use these state identifiers:

- INTENT_CAPTURED
- DESCRIPTION_DRAFTED
- MODEL_IN_PROGRESS
- MODEL_READY_FOR_VERIFICATION
- VERIFICATION_IN_PROGRESS
- VERIFICATION_FAILED
- VERIFIED
- IMPLEMENTATION_GENERATED
- TESTING
- READY_FOR_DEVELOPMENT
- DEPLOYED
- RETIRED

### State Semantics

- INTENT_CAPTURED  
  - Minimal project intent exists (title, goal).  
  - Next: DESCRIPTION_DRAFTED.

- DESCRIPTION_DRAFTED  
  - Coherent natural-language description exists.  
  - Next: MODEL_IN_PROGRESS (once first model is created).

- MODEL_IN_PROGRESS  
  - Workflow model/diagram exists and is being refined.  
  - Next: MODEL_READY_FOR_VERIFICATION (when user marks it “ready”).

- MODEL_READY_FOR_VERIFICATION  
  - Model is stable enough to verify.  
  - Next: VERIFICATION_IN_PROGRESS (when verification is triggered).  
  - Back: MODEL_IN_PROGRESS (if user decides to change model significantly).

- VERIFICATION_IN_PROGRESS  
  - Other agents (e.g. compliance, risk, architecture) are checking the model.  
  - Next: VERIFIED (if all required checks pass).  
  - Next: VERIFICATION_FAILED (if any required check fails).  
  - Back: MODEL_IN_PROGRESS (if verification is cancelled).

- VERIFICATION_FAILED  
  - Required verification has failed.  
  - Next: MODEL_IN_PROGRESS (user fixes issues then re-verifies).

- VERIFIED  
  - All required verification gates are passed.  
  - Next: IMPLEMENTATION_GENERATED (generate DSL / executable workflow).  
  - Note: Any material change to description/model must invalidate this state and return to MODEL_IN_PROGRESS.

- IMPLEMENTATION_GENERATED  
  - Executable workflow/DSL/config has been generated.  
  - Next: TESTING (create test instance and run tests).

- TESTING  
  - Workflow runs in non-production test/sandbox.  
  - Next: READY_FOR_DEVELOPMENT (tests and QA passed).  
  - Back: IMPLEMENTATION_GENERATED (fix implementation-only issues).  
  - Back: MODEL_IN_PROGRESS (if the underlying model is inadequate).

- READY_FOR_DEVELOPMENT  
  - All tests and approvals for deployment are met.  
  - Next: DEPLOYED (deployment performed).

- DEPLOYED  
  - Workflow is live in production.  
  - Next: RETIRED (when decommissioned).  
  - Changes typically start a new version at INTENT_CAPTURED or DESCRIPTION_DRAFTED.

- RETIRED  
  - Workflow is decommissioned.  
  - No further transitions, except optionally forking a new project from this baseline.

## Actions and Transitions

Define actions and allowed transitions roughly as:

- createDescription  
  - INTENT_CAPTURED → DESCRIPTION_DRAFTED

- createModel  
  - DESCRIPTION_DRAFTED → MODEL_IN_PROGRESS

- refineModel  
  - MODEL_IN_PROGRESS → MODEL_IN_PROGRESS (self-loop)

- markModelReadyForVerification  
  - MODEL_IN_PROGRESS → MODEL_READY_FOR_VERIFICATION

- startVerification  
  - MODEL_READY_FOR_VERIFICATION → VERIFICATION_IN_PROGRESS

- completeVerificationSuccess  
  - VERIFICATION_IN_PROGRESS → VERIFIED

- completeVerificationFailure  
  - VERIFICATION_IN_PROGRESS → VERIFICATION_FAILED

- cancelVerification  
  - VERIFICATION_IN_PROGRESS → MODEL_IN_PROGRESS

- generateImplementation  
  - VERIFIED → IMPLEMENTATION_GENERATED

- startTesting  
  - IMPLEMENTATION_GENERATED → TESTING

- completeTestingSuccess  
  - TESTING → READY_FOR_DEVELOPMENT

- fixImplementationIssue  
  - TESTING → IMPLEMENTATION_GENERATED

- fixModelIssue  
  - TESTING → MODEL_IN_PROGRESS

- deploy  
  - READY_FOR_DEVELOPMENT → DEPLOYED

- retire  
  - DEPLOYED → RETIRED

- deleteProject  
  - Any state → terminal deletion (outside main state machine).

## Global Rule: Description Revision

Implement a global action:

- reviseDescription  
  - Allowed from any state.
  - If the revision does *not* change workflow semantics, remain in the same state.
  - If the revision *does* change semantics (new actors, data uses, goals, risks):
    - Move back to MODEL_IN_PROGRESS.
    - Clear VERIFICATION_IN_PROGRESS, VERIFIED, and later approvals.
    - Require re-verification before proceeding to implementation/testing/deployment.