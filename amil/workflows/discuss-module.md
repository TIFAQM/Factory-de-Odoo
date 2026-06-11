# Workflow: discuss-module

Run an interactive module discussion to capture design decisions for a specific Odoo module.

**Rules:** Python tooling via `amil-utils orch`, atomic writes.

---

## Prerequisites

The module argument `{MODULE}` is provided by the user when invoking `/amil:discuss-module {module_name}`.

---

## Step 1: Validate Module Exists

Check that the module exists in module_status.json and is at "planned" status.

```bash
MODULE_STATUS=$(amil-utils orch module-status get "${MODULE}" --raw --cwd "$(pwd)" 2>&1)
```

- If command fails or returns empty: show error "Module '${MODULE}' not found in module_status.json. Run /amil:new-erp first." and STOP.
- Parse the JSON result. If `.status` is not "planned": show error "Module '${MODULE}' is at status '${status}', expected 'planned'. Discussion is only for modules at 'planned' status." and STOP.

## Step 2: Check for Existing CONTEXT.md

```bash
ls .planning/modules/${MODULE}/CONTEXT.md 2>/dev/null
```

- If file exists and has more than 5 lines of content: use AskUserQuestion to ask "CONTEXT.md already exists for ${MODULE}. What would you like to do?" with options: "overwrite" (start fresh), "skip" (keep existing, end workflow), "update" (append to existing).
- If "skip": STOP workflow (success, no changes).
- If "overwrite" or file doesn't exist: continue.
- If "update": note this in the agent prompt so it reads existing content first.

## Step 3: Detect Module Type

Use a lookup table to detect the module type from the module name:

MODULE_TYPES lookup:
- uni_core -> core
- uni_student -> student
- uni_fee -> fee
- uni_exam -> exam
- uni_faculty -> faculty
- uni_hr -> hr
- uni_timetable -> timetable
- uni_notification -> notification
- uni_portal -> portal

Detection logic:
1. Exact match against lookup keys
2. If no exact match, check if module name starts with any key followed by '_'
3. If no match at all, set type to null

If type detected: use AskUserQuestion to confirm: "Detected module type: **{type}**. Correct? (yes / change)"
- If "yes" or confirmed: proceed with detected type
- If "change" or user provides different type: use user's type

If type is null: use AskUserQuestion to ask user to select from: core, student, fee, exam, faculty, hr, timetable, notification, portal, generic

## Step 4: Load Question Template

Read the question template for the detected type from module-questions.json:

```
Read amil/references/module-questions.json
```

Extract ONLY the relevant type's entry (questions array + context_hints). If type not found in JSON, fall back to "generic".

## Step 5: Load Module Metadata

```bash
# Get decomposition entry for the module
Read .planning/research/decomposition.json
# Extract the module's entry: models, depends, complexity

# Get odoo config block
ODOO_CONFIG=$(amil-utils orch config-get odoo --raw --cwd "$(pwd)" 2>/dev/null || echo '{}')
```

## Step 6: Spawn Questioner Agent

Ensure module directory exists:
```bash
mkdir -p ".planning/modules/${MODULE}/"
```

Spawn the questioner agent via Task():

```
Task(
  prompt="You are discussing module design for ${MODULE_NAME} (type: ${MODULE_TYPE}).

MODULE METADATA:
---
Name: ${MODULE_NAME}
Type: ${MODULE_TYPE}
Models: ${MODELS_LIST}
Dependencies: ${DEPENDS_LIST}
Complexity: ${COMPLEXITY}
---

ODOO CONFIG:
---
${ODOO_CONFIG_JSON}
---

QUESTION TEMPLATE:
---
${QUESTIONS_JSON_FOR_TYPE}
---

CONTEXT HINTS:
---
${CONTEXT_HINTS}
---

${UPDATE_MODE_NOTE if applicable}

Follow your agent instructions to run the Q&A session and write CONTEXT.md to:
.planning/modules/${MODULE_NAME}/CONTEXT.md",
  subagent_type="amil-module-questioner",
  description="Module discussion: ${MODULE_NAME}"
)
```

## Step 7: Verify Output

After agent completes, verify the CONTEXT.md was written:

```bash
ls -la ".planning/modules/${MODULE}/CONTEXT.md"
```

If file exists and has content: report success.
If file missing: report error -- agent did not write output.

## Batch Mode (--batch flag)

When invoked with `--batch`:
1. Score all `planned` modules: `amil-utils orch spec score-all --raw --cwd "$(pwd)"` — returns `scores`, `batches` (grouped by discussion depth), and `summary`.
2. Group by tier and discussion depth (full < 40, brief 40-69, none >= 70)
3. Discuss 5 at a time (full) or 8 at a time (brief)
4. Present gaps as focused questions instead of full question templates
5. For brief discussions, suggest reasonable defaults the user can accept or override
6. Re-score after each batch — stop when all modules reach score >= 70

## Completion

Report to user:
- "Discussion complete for ${MODULE}."
- "Output: .planning/modules/${MODULE}/CONTEXT.md"
- "Next step: /amil:plan-module ${MODULE}"
