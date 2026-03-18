## Tech Search Keywords

This file is editorial source material only.

- Runtime search configuration lives in [search_query_registry.yaml](/Users/aarshad/dev/projects/blips/blips-ai-news-backend/src/backend/app/config/search_query_registry.yaml).
- Only the small `always_on` subset in that YAML is executed in v1. `story_only` and `parked` remain schema-supported editorial modes, but they do not participate in static scheduling yet.
- The Redis-unavailable fallback is deterministic: the planner derives a UTC time-window index from the surface cadence, then uses a weighted cycle plus a repetition guard in [video_discovery_service.py](/Users/aarshad/dev/projects/blips/blips-ai-news-backend/src/backend/app/services/video_discovery_service.py).
- If a surface ever drops to a single eligible static query, the repetition guard intentionally allows repeats. That is acceptable with the current registry size, but it becomes a tuning risk if the active pack count shrinks too far.
- User-visible feed changes are not instantaneous. Tiered feed responses are cached for `45s` in [tiered_feed_service.py](/Users/aarshad/dev/projects/blips/blips-ai-news-backend/src/backend/app/services/tiered_feed_service.py), so discovery improvements can lag slightly behind metrics and DB writes.
- The admin lane/query toggle lives in the backend-rendered admin UI at [ui.py](/Users/aarshad/dev/projects/blips/blips-ai-news-backend/src/backend/app/api/admin/ui.py).
- Future attribution path: search-discovered content now stores `ContentItem.discovered_via = yt_search:<query_id>` and discovery runs store `VideoDiscoveryRun.query_label = <query_id>`. That gives us a stable path to join promoted content and later user engagement data back to registry keywords/categories without adding a new DB table right now.

| Category | Rank | Search Term | Audience | Best Platform |
|---|---:|---|---|---|
| AI & Machine Learning | 1 | AI news | General tech audience, investors, students | Google News, X, YouTube |
| AI & Machine Learning | 2 | generative AI update | Tech workers, creators, students | Google News, YouTube, LinkedIn |
| AI & Machine Learning | 3 | frontier AI models | Researchers, investors, engineers | X, Google News, YouTube |
| AI & Machine Learning | 4 | agentic AI | Engineers, founders, investors | X, YouTube, LinkedIn |
| AI & Machine Learning | 5 | multimodal AI | Students, engineers, product people | YouTube, Google News, X |
| AI & Machine Learning | 6 | open source LLMs | Developers, startup founders, students | GitHub, X, YouTube |
| AI & Machine Learning | 7 | AI infrastructure | Investors, engineers, enterprise tech | Google News, X, LinkedIn |
| AI & Machine Learning | 8 | AI coding assistants | Developers, students, managers | YouTube, X, LinkedIn |
| AI & Machine Learning | 9 | enterprise AI adoption | Investors, founders, enterprise workers | LinkedIn, Google News, YouTube |
| AI & Machine Learning | 10 | AI monetization | Investors, founders, operators | Google News, LinkedIn, X |
| Semiconductors & Chips | 1 | semiconductor news | Investors, engineers, general tech audience | Google News, X, YouTube |
| Semiconductors & Chips | 2 | AI chips | Investors, AI engineers, hardware watchers | Google News, X, YouTube |
| Semiconductors & Chips | 3 | GPU demand | Investors, AI infra engineers | Google News, X, LinkedIn |
| Semiconductors & Chips | 4 | chip manufacturing | Students, engineers, investors | YouTube, Google News, X |
| Semiconductors & Chips | 5 | foundry update | Investors, supply chain watchers | Google News, X |
| Semiconductors & Chips | 6 | HBM market | Investors, chip analysts, infra engineers | X, Google News, LinkedIn |
| Semiconductors & Chips | 7 | RISC-V news | Engineers, students, hardware enthusiasts | X, YouTube, Google News |
| Semiconductors & Chips | 8 | ARM vs x86 | Students, developers, hardware audience | YouTube, X, Reddit |
| Semiconductors & Chips | 9 | lithography update | Engineers, investors, deep tech audience | Google News, X, YouTube |
| Semiconductors & Chips | 10 | semiconductor cycle | Investors, analysts | Google News, LinkedIn, X |
| Cloud & Infrastructure | 1 | cloud computing news | Engineers, students, investors | Google News, LinkedIn, YouTube |
| Cloud & Infrastructure | 2 | AWS news | Cloud engineers, architects | Google News, YouTube, X |
| Cloud & Infrastructure | 3 | Azure news | Enterprise tech workers, architects | LinkedIn, Google News, YouTube |
| Cloud & Infrastructure | 4 | Google Cloud news | Data teams, cloud engineers | Google News, LinkedIn, YouTube |
| Cloud & Infrastructure | 5 | Kubernetes news | DevOps, platform engineers, students | X, YouTube, LinkedIn |
| Cloud & Infrastructure | 6 | serverless trends | Developers, architects | YouTube, X, LinkedIn |
| Cloud & Infrastructure | 7 | platform engineering | Internal platform teams, DevOps | LinkedIn, X, YouTube |
| Cloud & Infrastructure | 8 | observability tools | SRE, DevOps, backend engineers | LinkedIn, X, YouTube |
| Cloud & Infrastructure | 9 | cloud security | Cloud teams, security teams | Google News, LinkedIn, X |
| Cloud & Infrastructure | 10 | data center expansion | Investors, infra engineers | Google News, X, LinkedIn |
| Cybersecurity | 1 | cybersecurity news | Security professionals, investors, students | Google News, X, LinkedIn |
| Cybersecurity | 2 | security breach news | Security teams, general tech audience | Google News, X, YouTube |
| Cybersecurity | 3 | ransomware trends | Security leaders, IT teams | Google News, LinkedIn, X |
| Cybersecurity | 4 | zero day news | Security researchers, engineers | X, Google News, Reddit |
| Cybersecurity | 5 | threat intelligence | Security teams, analysts | X, LinkedIn, Google News |
| Cybersecurity | 6 | zero trust security | Enterprise security, IT leaders | LinkedIn, Google News, YouTube |
| Cybersecurity | 7 | cloud security posture | Cloud security teams, DevSecOps | LinkedIn, X, YouTube |
| Cybersecurity | 8 | identity security | Enterprise IT, security teams | LinkedIn, Google News |
| Cybersecurity | 9 | API security | Developers, security engineers | X, YouTube, LinkedIn |
| Cybersecurity | 10 | cybersecurity market growth | Investors, founders, operators | Google News, LinkedIn |
| Software Development & Dev Tools | 1 | developer tools news | Developers, engineering managers | X, GitHub, YouTube |
| Software Development & Dev Tools | 2 | software engineering trends | Developers, students, managers | LinkedIn, YouTube, X |
| Software Development & Dev Tools | 3 | AI coding assistants | Developers, students | YouTube, X, LinkedIn |
| Software Development & Dev Tools | 4 | GitHub news | Developers, open source contributors | GitHub, X, LinkedIn |
| Software Development & Dev Tools | 5 | VS Code update | Developers, students | YouTube, X, GitHub |
| Software Development & Dev Tools | 6 | testing tools news | QA, developers, engineering leads | LinkedIn, X, YouTube |
| Software Development & Dev Tools | 7 | CI CD trends | DevOps, backend engineers | LinkedIn, X, YouTube |
| Software Development & Dev Tools | 8 | backend engineering trends | Backend developers, architects | X, YouTube, LinkedIn |
| Software Development & Dev Tools | 9 | frontend tooling news | Frontend developers, students | X, YouTube, GitHub |
| Software Development & Dev Tools | 10 | open source developer tools | Developers, students, founders | GitHub, X, YouTube |
| Data Engineering & Databases | 1 | data engineering news | Data engineers, analytics teams | LinkedIn, Google News, YouTube |
| Data Engineering & Databases | 2 | database news | DBAs, backend engineers, students | Google News, YouTube, X |
| Data Engineering & Databases | 3 | Apache Spark news | Data engineers, platform teams | LinkedIn, X, YouTube |
| Data Engineering & Databases | 4 | Kafka ecosystem update | Data engineers, streaming teams | X, LinkedIn, YouTube |
| Data Engineering & Databases | 5 | lakehouse trends | Data leaders, engineers, investors | LinkedIn, Google News, YouTube |
| Data Engineering & Databases | 6 | vector database news | AI engineers, data engineers | X, YouTube, Google News |
| Data Engineering & Databases | 7 | ETL ELT trends | Data engineers, analytics engineers | LinkedIn, YouTube |
| Data Engineering & Databases | 8 | data governance news | Data leaders, compliance teams | LinkedIn, Google News |
| Data Engineering & Databases | 9 | data observability | Data platform teams | LinkedIn, X, YouTube |
| Data Engineering & Databases | 10 | BI tools update | Analysts, business users, data teams | LinkedIn, YouTube, Google News |
| Consumer Tech | 1 | consumer tech news | General tech audience, creators | YouTube, Google News, X |
| Consumer Tech | 2 | gadget launches | Consumers, reviewers, tech fans | YouTube, Instagram, Google News |
| Consumer Tech | 3 | smartphone innovation | Consumers, mobile watchers | YouTube, Instagram, X |
| Consumer Tech | 4 | laptop trends | Students, professionals, buyers | YouTube, Google News |
| Consumer Tech | 5 | wearable tech update | Consumers, fitness tech audience | YouTube, Instagram, Google News |
| Consumer Tech | 6 | smart home tech news | Consumers, home tech buyers | YouTube, Google News, TikTok |
| Consumer Tech | 7 | AR glasses | Consumers, future tech audience | YouTube, X, Instagram |
| Consumer Tech | 8 | VR headset news | Gamers, tech fans, developers | YouTube, X, Google News |
| Consumer Tech | 9 | camera technology news | Creators, mobile tech audience | YouTube, Instagram, Google News |
| Consumer Tech | 10 | mobile OS update | Smartphone users, developers | Google News, YouTube, X |
| Mobile, Telecom & Connectivity | 1 | telecom tech news | Telecom professionals, investors | Google News, LinkedIn, X |
| Mobile, Telecom & Connectivity | 2 | 5G update | Telecom teams, consumers, investors | Google News, LinkedIn, YouTube |
| Mobile, Telecom & Connectivity | 3 | 6G research news | Researchers, telecom strategists | Google News, X, LinkedIn |
| Mobile, Telecom & Connectivity | 4 | Open RAN news | Telecom engineers, investors | LinkedIn, Google News, X |
| Mobile, Telecom & Connectivity | 5 | satellite internet news | General tech audience, telecom watchers | Google News, YouTube, X |
| Mobile, Telecom & Connectivity | 6 | Wi-Fi standards update | Network engineers, students | YouTube, Google News, X |
| Mobile, Telecom & Connectivity | 7 | private 5G | Enterprise IT, telecom professionals | LinkedIn, Google News |
| Mobile, Telecom & Connectivity | 8 | broadband innovation | Telecom audience, investors | Google News, LinkedIn |
| Mobile, Telecom & Connectivity | 9 | eSIM trends | Mobile users, telecom audience | Google News, YouTube, X |
| Mobile, Telecom & Connectivity | 10 | telecom infrastructure spending | Investors, analysts, telecom leaders | LinkedIn, Google News |
| Robotics & Automation | 1 | robotics news | Engineers, investors, general tech audience | Google News, YouTube, X |
| Robotics & Automation | 2 | automation trends | Operators, founders, engineers | LinkedIn, Google News, YouTube |
| Robotics & Automation | 3 | humanoid robot update | General tech audience, investors | YouTube, X, Google News |
| Robotics & Automation | 4 | industrial robot innovation | Manufacturing leaders, engineers | LinkedIn, YouTube, Google News |
| Robotics & Automation | 5 | warehouse automation news | Logistics leaders, investors | LinkedIn, Google News, YouTube |
| Robotics & Automation | 6 | embodied AI | AI researchers, robotics engineers | X, YouTube, Google News |
| Robotics & Automation | 7 | drone tech update | Consumers, engineers, industrial operators | YouTube, Google News, X |
| Robotics & Automation | 8 | robot vision | Robotics engineers, students | YouTube, X, LinkedIn |
| Robotics & Automation | 9 | service robots | Consumers, operators, investors | Google News, YouTube |
| Robotics & Automation | 10 | robotics market size | Investors, founders, analysts | Google News, LinkedIn |
| EV, Autonomous & Mobility Tech | 1 | EV technology update | Consumers, investors, engineers | Google News, YouTube, X |
| EV, Autonomous & Mobility Tech | 2 | autonomous driving trends | Investors, engineers, general tech audience | Google News, YouTube, X |
| EV, Autonomous & Mobility Tech | 3 | self driving news | Consumers, investors, automotive watchers | Google News, X, YouTube |
| EV, Autonomous & Mobility Tech | 4 | EV batteries | Investors, engineers, consumers | YouTube, Google News, X |
| EV, Autonomous & Mobility Tech | 5 | charging infrastructure news | EV owners, investors, policymakers | Google News, LinkedIn, YouTube |
| EV, Autonomous & Mobility Tech | 6 | ADAS trends | Automotive engineers, investors | Google News, YouTube, X |
| EV, Autonomous & Mobility Tech | 7 | software defined vehicle | Engineers, automotive strategists | LinkedIn, X, YouTube |
| EV, Autonomous & Mobility Tech | 8 | lidar news | Investors, engineers, autonomous vehicle watchers | X, Google News, YouTube |
| EV, Autonomous & Mobility Tech | 9 | connected car technology | Automotive professionals, developers | LinkedIn, YouTube, Google News |
| EV, Autonomous & Mobility Tech | 10 | battery chemistry update | Engineers, investors, students | YouTube, Google News, X |
| Quantum & Advanced Computing | 1 | quantum computing news | Researchers, investors, students | Google News, X, YouTube |
| Quantum & Advanced Computing | 2 | quantum breakthroughs | Researchers, general tech audience | Google News, X, YouTube |
| Quantum & Advanced Computing | 3 | quantum hardware news | Researchers, engineers, investors | Google News, X |
| Quantum & Advanced Computing | 4 | quantum software ecosystem | Developers, researchers, students | X, YouTube, LinkedIn |
| Quantum & Advanced Computing | 5 | quantum error correction | Researchers, advanced students | X, YouTube, papers |
| Quantum & Advanced Computing | 6 | post quantum cryptography | Security professionals, researchers | Google News, X, LinkedIn |
| Quantum & Advanced Computing | 7 | high performance computing news | Researchers, infra engineers | Google News, LinkedIn, X |
| Quantum & Advanced Computing | 8 | exascale computing update | Researchers, HPC engineers | Google News, YouTube |
| Quantum & Advanced Computing | 9 | neuromorphic computing | Researchers, deep tech audience | X, Google News, YouTube |
| Quantum & Advanced Computing | 10 | quantum startups funding | Investors, founders, researchers | LinkedIn, Google News |
| AR, VR, XR & Spatial Computing | 1 | AR VR news | Consumers, developers, investors | YouTube, Google News, X |
| AR, VR, XR & Spatial Computing | 2 | spatial computing trends | Investors, product teams, developers | LinkedIn, YouTube, Google News |
| AR, VR, XR & Spatial Computing | 3 | mixed reality news | General tech audience, developers | YouTube, X, Google News |
| AR, VR, XR & Spatial Computing | 4 | XR update | Developers, researchers, tech fans | X, YouTube, Google News |
| AR, VR, XR & Spatial Computing | 5 | AR glasses roadmap | Consumers, investors, product watchers | YouTube, X, Google News |
| AR, VR, XR & Spatial Computing | 6 | VR gaming trends | Gamers, creators, developers | YouTube, X, Reddit |
| AR, VR, XR & Spatial Computing | 7 | enterprise AR | Enterprise innovators, developers | LinkedIn, Google News |
| AR, VR, XR & Spatial Computing | 8 | immersive tech update | General tech audience, creators | YouTube, Instagram, X |
| AR, VR, XR & Spatial Computing | 9 | XR hardware launches | Consumers, developers, investors | YouTube, Google News |
| AR, VR, XR & Spatial Computing | 10 | spatial computing productivity | Product teams, enterprise workers | LinkedIn, YouTube |
| Blockchain, Web3 & Fintech | 1 | blockchain technology news | Investors, builders, students | Google News, X, YouTube |
| Blockchain, Web3 & Fintech | 2 | Web3 trends | Builders, investors, crypto audience | X, YouTube, Google News |
| Blockchain, Web3 & Fintech | 3 | fintech news | Investors, founders, operators | Google News, LinkedIn, X |
| Blockchain, Web3 & Fintech | 4 | digital payments innovation | Operators, fintech professionals | LinkedIn, Google News, YouTube |
| Blockchain, Web3 & Fintech | 5 | crypto infrastructure news | Builders, investors | X, Google News, YouTube |
| Blockchain, Web3 & Fintech | 6 | stablecoin infrastructure | Fintech builders, investors | X, LinkedIn, Google News |
| Blockchain, Web3 & Fintech | 7 | smart contracts | Developers, students, builders | X, GitHub, YouTube |
| Blockchain, Web3 & Fintech | 8 | DeFi technology update | Crypto builders, investors | X, YouTube, Google News |
| Blockchain, Web3 & Fintech | 9 | regtech update | Fintech operators, compliance teams | LinkedIn, Google News |
| Blockchain, Web3 & Fintech | 10 | digital identity fintech | Fintech builders, policy watchers | LinkedIn, Google News, X |
| Enterprise Software & SaaS | 1 | enterprise software news | Operators, investors, enterprise workers | LinkedIn, Google News |
| Enterprise Software & SaaS | 2 | SaaS trends | Investors, founders, GTM teams | LinkedIn, Google News, X |
| Enterprise Software & SaaS | 3 | B2B software update | Operators, product managers | LinkedIn, Google News |
| Enterprise Software & SaaS | 4 | digital transformation update | Enterprise leaders, consultants | LinkedIn, Google News |
| Enterprise Software & SaaS | 5 | workflow automation software | Operations teams, founders | LinkedIn, YouTube, Google News |
| Enterprise Software & SaaS | 6 | collaboration software news | Enterprise workers, IT buyers | LinkedIn, Google News |
| Enterprise Software & SaaS | 7 | enterprise search | Enterprise IT, knowledge workers | LinkedIn, YouTube |
| Enterprise Software & SaaS | 8 | knowledge management tools | Teams, operators, founders | LinkedIn, YouTube |
| Enterprise Software & SaaS | 9 | customer support AI tools | Support leaders, operators | LinkedIn, YouTube, Google News |
| Enterprise Software & SaaS | 10 | SaaS market outlook | Investors, founders, analysts | Google News, LinkedIn |
| Startups, VC & Innovation | 1 | tech startup news | Founders, investors, students | Google News, LinkedIn, X |
| Startups, VC & Innovation | 2 | startup funding rounds | Investors, founders, operators | LinkedIn, Google News, X |
| Startups, VC & Innovation | 3 | venture capital tech | Investors, founders | LinkedIn, X, podcasts |
| Startups, VC & Innovation | 4 | AI startup funding | Investors, founders, builders | Google News, LinkedIn, X |
| Startups, VC & Innovation | 5 | deep tech startups | Investors, researchers, founders | LinkedIn, Google News |
| Startups, VC & Innovation | 6 | startup acquisitions | Investors, operators, founders | Google News, LinkedIn |
| Startups, VC & Innovation | 7 | startup product market fit | Founders, product managers, students | YouTube, LinkedIn |
| Startups, VC & Innovation | 8 | founder interviews | Founders, students, operators | YouTube, podcasts, LinkedIn |
| Startups, VC & Innovation | 9 | IPO pipeline tech | Investors, founders, analysts | Google News, LinkedIn |
| Startups, VC & Innovation | 10 | M&A tech analysis | Investors, operators | Google News, LinkedIn |
| Big Tech Company Tracking | 1 | Apple latest tech strategy | Investors, product people, consumers | Google News, YouTube, X |
| Big Tech Company Tracking | 2 | Microsoft AI strategy | Investors, enterprise workers, developers | LinkedIn, Google News, YouTube |
| Big Tech Company Tracking | 3 | Google AI roadmap | Developers, investors, product teams | Google News, YouTube, X |
| Big Tech Company Tracking | 4 | Amazon cloud news | Cloud teams, investors | Google News, LinkedIn, YouTube |
| Big Tech Company Tracking | 5 | Meta AI and VR update | Creators, investors, developers | Google News, X, YouTube |
| Big Tech Company Tracking | 6 | Nvidia ecosystem update | Investors, AI engineers | Google News, X, YouTube |
| Big Tech Company Tracking | 7 | AMD data center update | Investors, infra engineers | Google News, X |
| Big Tech Company Tracking | 8 | Intel foundry strategy | Investors, hardware audience | Google News, LinkedIn, X |
| Big Tech Company Tracking | 9 | OpenAI product update | Developers, investors, general tech audience | X, Google News, YouTube |
| Big Tech Company Tracking | 10 | Tesla autonomy update | Investors, consumers, autonomy watchers | X, Google News, YouTube |
| Regulation, Policy & Legal Tech | 1 | tech regulation news | Investors, policy watchers, founders | Google News, LinkedIn, X |
| Regulation, Policy & Legal Tech | 2 | AI regulation update | AI builders, investors, policy audience | Google News, LinkedIn, X |
| Regulation, Policy & Legal Tech | 3 | antitrust tech news | Investors, policy watchers, legal audience | Google News, LinkedIn |
| Regulation, Policy & Legal Tech | 4 | privacy law tech impact | Operators, compliance teams, founders | LinkedIn, Google News |
| Regulation, Policy & Legal Tech | 5 | chip export restrictions | Investors, semiconductor watchers | Google News, X, LinkedIn |
| Regulation, Policy & Legal Tech | 6 | cybersecurity regulation | Security leaders, compliance teams | LinkedIn, Google News |
| Regulation, Policy & Legal Tech | 7 | digital policy trends | Policy audience, tech leaders | LinkedIn, Google News |
| Regulation, Policy & Legal Tech | 8 | AI safety policy | Researchers, policymakers, AI teams | Google News, X, LinkedIn |
| Regulation, Policy & Legal Tech | 9 | copyright and AI law | Creators, AI builders, legal audience | Google News, LinkedIn, X |
| Regulation, Policy & Legal Tech | 10 | platform regulation | Big tech watchers, founders, investors | Google News, LinkedIn |
| Green Tech / Climate Tech / Energy Tech | 1 | climate tech news | Investors, founders, researchers | Google News, LinkedIn, X |
| Green Tech / Climate Tech / Energy Tech | 2 | clean energy tech trends | Investors, students, policy audience | Google News, LinkedIn, YouTube |
| Green Tech / Climate Tech / Energy Tech | 3 | battery storage news | Investors, engineers, energy audience | Google News, YouTube, X |
| Green Tech / Climate Tech / Energy Tech | 4 | fusion energy update | Researchers, investors, deep tech audience | Google News, X, YouTube |
| Green Tech / Climate Tech / Energy Tech | 5 | solar tech innovation | Energy audience, students, investors | Google News, YouTube |
| Green Tech / Climate Tech / Energy Tech | 6 | grid technology | Energy professionals, policy audience | LinkedIn, Google News |
| Green Tech / Climate Tech / Energy Tech | 7 | data center energy efficiency | Infra leaders, investors | LinkedIn, Google News |
| Green Tech / Climate Tech / Energy Tech | 8 | sustainable computing | Enterprise tech, researchers | LinkedIn, Google News |
| Green Tech / Climate Tech / Energy Tech | 9 | carbon capture tech | Investors, deep tech audience | Google News, YouTube |
| Green Tech / Climate Tech / Energy Tech | 10 | climate tech funding | Investors, founders | LinkedIn, Google News |
| EdTech & Learning Tech | 1 | edtech news | Educators, founders, investors | Google News, LinkedIn, YouTube |
| EdTech & Learning Tech | 2 | AI in education | Educators, students, founders | Google News, YouTube, LinkedIn |
| EdTech & Learning Tech | 3 | learning technology trends | Educators, operators, investors | LinkedIn, Google News |
| EdTech & Learning Tech | 4 | AI tutors | Students, parents, educators | YouTube, TikTok, Google News |
| EdTech & Learning Tech | 5 | coding education tools | Students, teachers, parents | YouTube, LinkedIn, Google News |
| EdTech & Learning Tech | 6 | student productivity apps | Students, creators | TikTok, YouTube, Instagram |
| EdTech & Learning Tech | 7 | online learning platform update | Students, educators | Google News, YouTube |
| EdTech & Learning Tech | 8 | certification platforms | Students, career switchers | LinkedIn, YouTube |
| EdTech & Learning Tech | 9 | gamified learning tech | Students, educators, founders | YouTube, TikTok, Google News |
| EdTech & Learning Tech | 10 | digital classrooms innovation | Educators, schools, founders | LinkedIn, Google News |
| Future of Work & Tech Careers | 1 | tech job market news | Students, professionals, investors | Google News, LinkedIn, YouTube |
| Future of Work & Tech Careers | 2 | future of work tech | Operators, professionals, founders | LinkedIn, Google News |
| Future of Work & Tech Careers | 3 | automation and jobs | Students, professionals, policymakers | Google News, YouTube, LinkedIn |
| Future of Work & Tech Careers | 4 | workplace AI tools | Professionals, managers, founders | LinkedIn, YouTube, X |
| Future of Work & Tech Careers | 5 | software engineering hiring trends | Developers, students, recruiters | LinkedIn, Google News, X |
| Future of Work & Tech Careers | 6 | AI skill demand | Students, professionals, educators | LinkedIn, Google News |
| Future of Work & Tech Careers | 7 | cloud jobs demand | Students, professionals | LinkedIn, YouTube |
| Future of Work & Tech Careers | 8 | cybersecurity jobs | Students, career switchers | LinkedIn, YouTube, Google News |
| Future of Work & Tech Careers | 9 | best tech skills to learn | Students, career switchers | YouTube, TikTok, LinkedIn |
| Future of Work & Tech Careers | 10 | tech career roadmap | Students, early career professionals | YouTube, LinkedIn |
| Product Launches & Events | 1 | tech launch event | General tech audience, creators | YouTube, Google News, X |
| Product Launches & Events | 2 | keynote highlights | General tech audience, investors | YouTube, X, Google News |
| Product Launches & Events | 3 | developer conference recap | Developers, product teams, students | YouTube, X, LinkedIn |
| Product Launches & Events | 4 | CES tech highlights | Consumers, investors, creators | YouTube, Google News, Instagram |
| Product Launches & Events | 5 | MWC mobile tech update | Mobile audience, telecom professionals | YouTube, Google News, X |
| Product Launches & Events | 6 | GTC AI announcements | AI engineers, investors | X, YouTube, Google News |
| Product Launches & Events | 7 | WWDC highlights | Apple developers, consumers | YouTube, X, Google News |
| Product Launches & Events | 8 | Google I/O recap | Developers, product managers | YouTube, X, Google News |
| Product Launches & Events | 9 | Build conference highlights | Developers, enterprise workers | YouTube, LinkedIn, X |
| Product Launches & Events | 10 | re:Invent announcements | Cloud engineers, architects | YouTube, LinkedIn, X |
| Research & Emerging Tech | 1 | emerging tech news | Investors, researchers, general tech audience | Google News, X, YouTube |
| Research & Emerging Tech | 2 | research breakthrough technology | Students, researchers, science audience | Google News, YouTube, X |
| Research & Emerging Tech | 3 | deep tech innovation | Investors, founders, researchers | LinkedIn, Google News |
| Research & Emerging Tech | 4 | brain computer interface | Researchers, students, deep tech audience | YouTube, X, Google News |
| Research & Emerging Tech | 5 | photonics computing | Researchers, engineers, investors | Google News, X |
| Research & Emerging Tech | 6 | privacy preserving AI | AI researchers, enterprise tech audience | X, LinkedIn, Google News |
| Research & Emerging Tech | 7 | federated learning | AI engineers, researchers, students | X, YouTube, papers |
| Research & Emerging Tech | 8 | computer vision breakthroughs | AI engineers, students, researchers | X, YouTube, Google News |
| Research & Emerging Tech | 9 | digital twins | Enterprise innovators, engineers | LinkedIn, YouTube, Google News |
| Research & Emerging Tech | 10 | advanced materials tech | Researchers, investors, students | Google News, YouTube |
