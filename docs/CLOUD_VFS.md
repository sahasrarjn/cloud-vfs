# cloud-vfs workflow

See the [main README](../README.md). Summary:

```bash
cloud-vfs ensure <path>           # fetch if stub/missing
cloud-vfs resolve <path>          # JSON instructions
cloud-vfs status                    # local vs stub + sizes
cloud-vfs offload --dry-run         # preview
cloud-vfs offload <paths>           # explicit upload + stub
```

Manual control only — no auto-tracking, no cron.
