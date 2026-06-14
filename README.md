# SRE Second Brain

A Backstage-anchored internal developer platform POC, running on a local [kind](https://kind.sigs.k8s.io/) cluster. See `docs/specs/project-spec.md` for the full design.

## Prerequisites

`kind`, `kubectl`, `docker`, `helm`, and `yarn` (via Corepack) on your PATH, with the Docker daemon running. Verify with:

```sh
make preflight
```

## Bring up Backstage

```sh
make up               # create the kind cluster + namespaces
make ingress-install  # install ingress-nginx and wait for it
make backstage-up     # build, load and deploy Backstage
```

Then open **http://localhost:3000** and sign in as a guest.

> The first `make backstage-up` is slow — it runs a full `yarn install` and Backstage build before building the image.

## Other commands

```sh
make help     # list all targets
make status   # show cluster, nodes and namespaces
make down     # delete the cluster
make nuke     # delete the cluster and prune images for a cold rebuild
```

## GitHub auth (optional)

Guest login works out of the box. For GitHub OAuth, create `backstage/.env` with `AUTH_GITHUB_CLIENT_ID` and `AUTH_GITHUB_CLIENT_SECRET`, then:

```sh
make backstage-secret
make backstage-deploy
```
