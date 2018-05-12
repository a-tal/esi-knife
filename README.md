# ESI-knife

It's like jackknife, but for ESI.

## What does this do?

This will fetch all data about your character, your corporation and/or alliance that it can from ESI. It will compress and save this content into a file, which you could then share and/use with other applications.

This is a web front end to the above functionality. A command line interface is planned to be re-introduced soonish (prototype was a CLI).

Large corporations think about running your own please. This is self-hosted, please don't chew through my bandwidth.

## Is this official?

No. Not in any way shape or form. This is entirely a personal project.

## Getting started

 1. Run `docker-compose up`
 2. Go to http://localhost:8888/

Everything should "just work". If you need to adjust the SSO callback or exposed port, the environment variables are configured in the `docker-compose.yaml` file.

## TODOs

If you want to help out with something from here pull requests are very welcomed.

- javascript parse and display the /view results
- automatic x-pages expansion
- requests client caching
- styling is kinda p bad in general

There could also maybe be some routes missing, I threw this together in an evening after work. I did not look at each route and consider their usefulness, but rather went through by path parameters requested. Should be pretty close to all the character-specific data though. Feel free to open an issue here if I've missed something though.
