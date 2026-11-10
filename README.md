# k8s-explain

Pipe kubectl output in, get a plain-English diagnosis, likely fix and verification commands out.

> **Project status: planning. There is no working code in this repository yet.**
> This README describes what the project will do. Nothing below can be installed or run today.
> Features are ticked off in the checklist as they are built.

## What it is

A command-line tool.

**The problem it solves:** Kubernetes errors are cryptic and debugging them depends on tribal knowledge.

## Who it is for

Anyone debugging a Kubernetes workload who wants a quick, readable diagnosis.

## How it will work

1. You pipe the output of kubectl describe, events or logs into the tool.
2. It explains in plain English what is wrong and the most likely cause.
3. It suggests a fix and the commands to confirm it; the tool itself never changes your cluster.

## Planned features

Unticked items are not built yet.

- [ ] Reads kubectl describe, events and logs from stdin
- [ ] Plain-English diagnosis
- [ ] Likely fix with commands
- [ ] Verification steps
- [ ] Read-only by design

## Planned usage

This is the intended interface, shown so you can judge the design. **It does not work yet**, and names may change before the first release.

```bash
kubectl describe pod api-7d9f | k8s-explain
```

## Contributing

Feedback on the design is the most useful contribution right now. Open an issue to suggest a feature, question an approach or describe your use case. Code contributions are welcome once the first version lands.

## Author

Built and maintained by [Narayana Vemburaj](https://github.com/narayana006).

## License

[MIT](LICENSE)
