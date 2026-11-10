# k8s-explain

> Pipe kubectl output in, get a plain-English diagnosis, likely fix and verification commands out.

**Status:** 🚧 Early stage — design and roadmap published, implementation in progress. Stars, issues and ideas welcome.

## Why

Kubernetes errors are cryptic and debugging them depends on tribal knowledge.

## Planned features

- [ ] Reads kubectl describe, events and logs from stdin
- [ ] Plain-English diagnosis
- [ ] Likely fix with commands
- [ ] Verification steps
- [ ] Read-only by design

## Intended usage

```
kubectl describe pod api-7d9f | k8s-explain
```

_The interface above is the target design and may change._

## Roadmap

1. Core implementation of the first feature set
2. Examples and tests
3. CI and first tagged release
4. Docs and integrations

## Contributing

Contributions are welcome. Open an issue to discuss an idea or pick an unchecked item from the feature list.

## License

[MIT](LICENSE)
