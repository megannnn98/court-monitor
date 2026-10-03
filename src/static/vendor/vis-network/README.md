# vis-network

The browser bundle of [vis-network](https://github.com/visjs/vis-network) 10.1.2, served
from here so the console does not depend on a CDN while it runs.

- `vis-network.min.js` is `standalone/umd/vis-network.min.js` of the npm package
  `vis-network@10.1.2`, unchanged. It defines the global `vis` and carries its own styles.
- Licence: Apache-2.0 or MIT, at the user's choice; both texts are beside this file.

To update: `npm pack vis-network@<version>`, unpack, copy the same file over this one and
change the version here. Nothing in this repository builds JavaScript.
