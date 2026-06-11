"""Stage 6 LoRA training pipeline modules.

Per ``docs/pre-fill-intent-classifier-design/05-training-pipeline.md``:

- ``trace_export``     turn_traces → JSONL ({context, input_window, labels})
- ``teacher_relabel``  WI-6.2: high-precision Vertex Gemini Pro relabel
- ``curate``           WI-6.3: dedup / leakage-scan / confusion oversampling
- ``train_lora``       WI-6.4: PEFT runner

This package only ships ``trace_export`` so far (WI-6.1). The rest are
stubs until their work items are picked up.
"""
