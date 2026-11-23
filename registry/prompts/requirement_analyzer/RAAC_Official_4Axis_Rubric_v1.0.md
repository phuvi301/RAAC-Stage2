# RAAC — Official 4-Axis Requirement Rubric

**Version:** 1.0  
**Purpose:** Official annotation rubric for the RAAC Requirement Profile  
**Axes:** Complexity (C), Testability (T), Security (S), Architecture Impact (A)  
**Scale:** 1–5 for each axis

---

## 1. Mục đích và phạm vi

Rubric này được dùng để đánh giá một requirement/issue theo **4 trục độc lập**:

1. **Complexity (C)** — độ phức tạp khi hiện thực hóa requirement.
2. **Testability (T)** — độ khó khi kiểm chứng requirement đã được hiện thực đúng và đầy đủ.
3. **Security (S)** — mức độ requirement liên quan đến bảo mật, quyền truy cập, dữ liệu nhạy cảm và rủi ro an ninh.
4. **Architecture Impact (A)** — mức độ requirement làm thay đổi cấu trúc, ranh giới, giao tiếp hoặc quyết định kiến trúc.

Mỗi trục được chấm từ **1 đến 5**, kèm một **description/evidence** ngắn giải thích lý do.

Rubric được thiết kế để dùng chung cho:

- Human annotators tạo **ground-truth labels**.
- Requirement Analyzer LLM tạo **Requirement Profile**.
- So sánh mức độ nhất quán giữa người và Analyzer.
- Làm đầu vào cho các thí nghiệm RAAC về **adaptive collaboration**.

> **Requirement Profile:** `(C, T, S, A)`, trong đó mỗi giá trị thuộc `[1,5]`.

---

# 2. Ranh giới giữa 4 trục

| Trục | Câu hỏi chính | Không tính vào trục này |
|---|---|---|
| **Complexity (C)** | Để hiện thực đúng requirement này khó về kỹ thuật/tổ chức đến đâu? | Độ khó kiểm thử, security risk, architectural impact |
| **Testability (T)** | Để verify requirement đã đúng và đầy đủ khó đến đâu? | Độ khó implement, security risk |
| **Security (S)** | Requirement tác động đến bảo mật, quyền, dữ liệu nhạy cảm và threat đến đâu? | Implementation complexity, architecture impact thuần túy |
| **Architecture Impact (A)** | Requirement làm thay đổi cấu trúc, boundary, integration hoặc quyết định kiến trúc đến đâu? | Implementation effort, security risk thuần túy |

**Quy tắc quan trọng:** một yếu tố có thể liên quan đến nhiều khía cạnh trong thực tế, nhưng khi chấm phải xác định **nó đang được dùng làm bằng chứng cho axis nào**.

---

# 3. Complexity (C)

## 3.1. Định nghĩa

**Complexity** là mức độ khó khăn về **kỹ thuật và tổ chức** để hiện thực hóa đúng requirement.

Trục này phản ánh quy mô thay đổi, business logic, dependency, integration, dữ liệu, concurrency, technical uncertainty và domain knowledge.

Không dùng Complexity để đánh giá:

- requirement khó test đến đâu → **Testability**
- requirement nguy hiểm về security đến đâu → **Security**
- requirement làm thay đổi kiến trúc đến đâu → **Architecture Impact**

## 3.2. Các yếu tố đánh giá

| Yếu tố | Nội dung |
|---|---|
| **Scope of impact** | Số module/component/service bị tác động |
| **I/O complexity** | Số lượng và loại input/output, format và transformation |
| **Business logic** | Số rule, decision point, exception flow, priority hoặc thuật toán |
| **Dependencies** | Dependency với requirement, component, API hoặc service khác |
| **Actors / flows** | Số actor/stakeholder và alternate/exception flows |
| **Integration** | Mức độ tích hợp với hệ thống/API/bên thứ ba |
| **Concurrency / event ordering** | Xử lý đồng thời, duplicate event, ordering, race condition |
| **Data / entities** | Số thực thể, transformation, integrity constraint, synchronization |
| **NFR interaction** | Performance, availability, consistency, reliability... phải đáp ứng đồng thời |
| **Technical uncertainty** | Công nghệ mới, pattern chưa có tiền lệ, cần POC/spike |
| **Domain knowledge** | Mức chuyên môn nghiệp vụ cần thiết |
| **Compliance constraints** | Chuẩn/pháp lý tạo thêm điều kiện kỹ thuật phải đáp ứng |

## 3.3. Thang điểm

### **1 — Rất thấp**

Requirement:

- thay đổi cục bộ trong một component;
- logic tuyến tính, ít hoặc không có branch;
- không có dependency đáng kể;
- không cần integration mới;
- dùng pattern/cơ chế đã có;
- dữ liệu đơn giản;
- không có NFR đặc biệt.

Ví dụ: thêm một trường đơn giản vào form CRUD hiện có.

### **2 — Thấp**

Requirement:

- chủ yếu nằm trong một module;
- có một số điều kiện/exception đơn giản;
- dependency ít và rõ ràng;
- có thể cần điều chỉnh nhẹ pattern hiện tại;
- dữ liệu và flow tương đối đơn giản;
- không có integration hoặc concurrency đáng kể.

### **3 — Trung bình**

Requirement:

- liên quan nhiều component/module;
- có nhiều business rule hoặc alternate flows;
- có nhiều dependency hoặc một số integration;
- cần xử lý trạng thái, transaction hoặc event ordering ở mức xác định;
- có nhiều entity/data transformation;
- nhiều ràng buộc kỹ thuật cần phối hợp;
- có thể cần thử nghiệm một giải pháp mới trong phạm vi dự án.

### **4 — Cao**

Requirement:

- xuyên nhiều module/service;
- business logic hoặc state flow phức tạp;
- dependency chain đáng kể;
- cần xử lý partial failure, duplicate/out-of-order messages hoặc recovery;
- tích hợp nhiều hệ thống;
- có nhiều NFR tương tác;
- yêu cầu domain knowledge sâu hoặc giải pháp kỹ thuật mới đáng kể.

### **5 — Rất cao**

Requirement:

- tác động ở quy mô toàn hệ thống hoặc nhiều hệ thống;
- logic/trạng thái phụ thuộc lẫn nhau khó phân tách;
- distributed transaction, recovery hoặc concurrency phức tạp;
- data migration/synchronization xuyên hệ thống;
- nhiều NFR nghiêm ngặt và có trade-off;
- công nghệ/giải pháp chưa có tiền lệ phù hợp, cần POC/research;
- yêu cầu chuyên môn sâu từ nhiều domain.

---

# 4. Testability (T)

## 4.1. Định nghĩa

**Testability** là mức độ khó khăn để **kiểm chứng requirement đã được hiện thực đúng và đầy đủ**.

Trục này tập trung vào khả năng:

- xác định expected result;
- quan sát kết quả;
- tái tạo điều kiện test;
- xác định pass/fail;
- tự động hóa kiểm thử;
- kiểm soát nondeterminism;
- giải quyết test oracle.

Testability **không phải** implementation complexity.

## 4.2. Các yếu tố đánh giá

| Yếu tố | Nội dung |
|---|---|
| **Measurability** | Requirement có metric/threshold rõ ràng hay chỉ mô tả định tính |
| **Weak phrases** | Các cụm như “adequate”, “appropriate”, “user-friendly”, “effective”... nhưng không có định nghĩa đo lường |
| **Acceptance criteria** | Có thể xác định rõ expected result/pass/fail hay không |
| **Observability** | Output/hành vi có quan sát được qua UI, API, log, DB hoặc telemetry hay không |
| **Reproducibility** | Có dễ tái tạo đúng input, state, timing và environment không |
| **Test dependencies** | Có cần mock, stub, sandbox, external system hoặc test data đặc biệt không |
| **Determinism** | Kết quả có ổn định giữa các lần chạy hay bị ảnh hưởng bởi concurrency, random, time, AI/ML... |
| **Test space** | Số lượng branch, combination, edge case cần kiểm thử |
| **Subjectivity** | Requirement có phụ thuộc đánh giá chủ quan của người dùng không |
| **Test oracle** | Có xác định được thế nào là “đúng” để so sánh hay không |
| **Test effort/tooling** | Có cần infrastructure hoặc tooling đặc biệt để verify không |

## 4.3. Thang điểm

### **1 — Rất thấp**

Requirement:

- có metric hoặc pass/fail rõ ràng;
- expected result xác định;
- output quan sát trực tiếp;
- deterministic;
- test data đơn giản;
- không cần môi trường đặc biệt;
- có thể viết automated test ngay.

### **2 — Thấp**

Requirement:

- acceptance criteria rõ;
- output có thể quan sát;
- cần một lượng nhỏ test data/mock/stub;
- môi trường test có sẵn;
- phần lớn có thể tự động hóa;
- kết quả gần như deterministic.

### **3 — Trung bình**

Requirement có một hoặc nhiều yếu tố:

- cần instrumentation/log bổ sung;
- cần dựng state hoặc test data cụ thể;
- có một phần tiêu chí cần diễn giải;
- có weak phrase nhưng vẫn có thể làm rõ;
- cần mock/stub/external dependency;
- có timing/order cần kiểm soát;
- test space tương đối lớn;
- cần một phần manual/exploratory testing.

### **4 — Cao**

Requirement:

- có behavior khó quan sát;
- khó tái tạo đúng điều kiện;
- phụ thuộc external system/sandbox phức tạp;
- acceptance criteria khó lượng hóa;
- có nondeterminism đáng kể: concurrency, random, distributed timing, AI/ML;
- cần đánh giá theo threshold/statistical criteria;
- test space lớn, khó phủ đầy đủ.

### **5 — Rất cao**

Requirement:

- không có tiêu chí “đúng” rõ ràng;
- chứa weak phrase nghiêm trọng nhưng không có metric/acceptance criteria;
- output gần như không observable;
- điều kiện thực tế rất khó hoặc không thể tái tạo;
- phụ thuộc nhiều hệ thống ngoài khó kiểm soát;
- nondeterminism rất cao;
- test oracle chưa có lời giải rõ ràng;
- không gian test có tính tổ hợp/bùng nổ, chỉ có thể kiểm thử một phần.

> Khi chấm **5** vì thiếu acceptance criteria, description nên chỉ rõ phần nào của requirement cần được làm rõ/thêm metric.

---

# 5. Security (S)

## 5.1. Định nghĩa

**Security** là mức độ requirement tác động đến:

- confidentiality;
- integrity;
- authentication;
- authorization;
- privacy;
- auditability;
- attack surface;
- threat resistance;
- security/compliance controls.

Không dùng Security để đánh giá implementation complexity hoặc architecture impact thuần túy.

## 5.2. Các yếu tố đánh giá

| Yếu tố | Nội dung |
|---|---|
| **Data sensitivity** | Public, internal, PII, financial, health, credential, secret... |
| **Attack surface** | API/endpoint/network exposure, untrusted input |
| **Authentication** | Login, identity, MFA, token/session |
| **Authorization** | Role, permission, access policy |
| **Data integrity** | Khả năng dữ liệu bị sửa sai/trái phép |
| **Privacy** | Encryption, masking, anonymization, retention |
| **Threat exposure** | Injection, abuse, brute force, replay, fraud, DoS... |
| **Auditability** | Logging, traceability, forensic evidence |
| **Trust boundaries** | Client/server, service/service, third-party boundaries |
| **Compliance** | Legal/regulatory/security standards |
| **CIA impact** | Confidentiality, Integrity, Availability |
| **Security control role** | Requirement có phải security control lõi hay không |

## 5.3. Thang điểm

### **1 — Rất thấp**

- dữ liệu công khai hoặc không nhạy cảm;
- không có authentication/authorization đáng kể;
- không mở attack surface mới;
- không xử lý security control;
- không có compliance đặc biệt.

### **2 — Thấp**

- dữ liệu nội bộ hoặc ít nhạy cảm;
- dùng authentication/authorization hiện có;
- có validation và access control cơ bản;
- chỉ phát sinh rủi ro cục bộ;
- áp dụng security mechanisms đã có.

### **3 — Trung bình**

- xử lý PII hoặc dữ liệu nội bộ quan trọng;
- có endpoint/integration cần bảo vệ;
- có nhiều role/permission;
- cần encryption/masking/retention;
- cần audit cho thao tác nhạy cảm;
- có threat thông thường cần xử lý.

### **4 — Cao**

- liên quan trực tiếp authentication/authorization/session;
- xử lý dữ liệu nhạy cảm;
- mở rộng attack surface đáng kể;
- security policy phức tạp/fine-grained;
- cần threat modeling hoặc security review chuyên sâu;
- có compliance/security control đáng kể.

### **5 — Rất cao**

- là security control lõi hoặc nằm tại trust boundary quan trọng;
- liên quan identity/security architecture trung tâm;
- xử lý dữ liệu cực kỳ nhạy cảm;
- access control/cryptography/key management quan trọng;
- failure có thể gây hậu quả nghiêm trọng trên diện rộng;
- yêu cầu security review/audit/pentest hoặc compliance nghiêm ngặt.

---

# 6. Architecture Impact (A)

## 6.1. Định nghĩa

**Architecture Impact** là mức độ requirement làm thay đổi:

- system structure;
- component/service boundary;
- API/interface contract;
- data architecture;
- integration/middleware;
- deployment/infrastructure;
- quality attributes;
- architectural decisions;
- reversibility của quyết định.

Trục này tập trung vào **hệ quả kiến trúc**, không phải effort triển khai.

## 6.2. Các yếu tố đánh giá

| Yếu tố | Nội dung |
|---|---|
| **System structure** | Module/component/service/layer |
| **System boundary** | Responsibility và boundary giữa các thành phần |
| **Data architecture** | Schema, ownership, storage, consistency, data flow |
| **Integration/middleware** | API, message broker, communication mechanism |
| **Deployment/infra** | Topology, cloud resource, container, network, deployment strategy |
| **Quality attributes** | Performance, scalability, availability, reliability, maintainability |
| **Crosscutting concern** | Logging, security, observability, i18n... |
| **Architectural decision** | Có cần decision/ADR mới hay thay đổi architectural pattern không |
| **Business value / technical risk** | Mức độ quan trọng và rủi ro kỹ thuật của quyết định |
| **Stakeholder impact** | Có liên quan stakeholder có quyền quyết định kiến trúc không |
| **Reversibility** | Dễ rollback hay tạo quyết định khó đảo ngược |

## 6.3. Thang điểm

### **1 — Rất thấp**

- thay đổi bên trong một component hiện có;
- không đổi API contract;
- không đổi shared data model;
- không ảnh hưởng deployment;
- không tạo architectural decision mới;
- dễ rollback.

### **2 — Thấp**

- thay đổi một module/vài component;
- thêm field hoặc endpoint theo pattern hiện có;
- integration hiện có chỉ điều chỉnh nhẹ;
- ảnh hưởng nhỏ đến một quality attribute;
- vẫn dễ đảo ngược.

### **3 — Trung bình**

- thay đổi nhiều component hoặc một subsystem;
- thay đổi API contract giữa các thành phần;
- thêm component/service theo architectural pattern có sẵn;
- thay đổi schema/data flow trong subsystem;
- thêm integration hoặc communication mechanism;
- có ảnh hưởng rõ đến một hoặc vài quality attributes;
- cần migration/rollback plan nhưng vẫn kiểm soát được.

### **4 — Cao**

- ảnh hưởng nhiều service/subsystem;
- thay đổi responsibility/boundary quan trọng;
- thay đổi data ownership hoặc shared contract;
- cần architectural decision/ADR mới;
- thay đổi architectural pattern, ví dụ sync → async, thêm message queue/cache layer;
- ảnh hưởng mạnh đến scalability, availability, performance hoặc reliability;
- khó rollback, cần compatibility/migration strategy.

### **5 — Rất cao**

- thay đổi architectural style hoặc nền tảng kiến trúc;
- thay đổi system boundary/domain decomposition;
- tái thiết kế data architecture hoặc consistency model;
- tái cấu trúc integration giữa nhiều hệ thống;
- thay đổi deployment/infrastructure architecture trên toàn hệ thống;
- tác động đồng thời đến nhiều quality attributes cốt lõi;
- quyết định rất khó đảo ngược và tạo ràng buộc lâu dài.

---

# 7. Ví dụ minh họa

## Requirement

> “Add an API endpoint that allows authenticated users to update their profile email address. The new email must be verified before replacing the existing address.”

### Annotation

| Axis | Score | Evidence |
|---|---:|---|
| Complexity | 3 | Có authentication, email verification flow và trạng thái chuyển đổi giữa email cũ/mới; cần phối hợp nhiều bước xử lý. |
| Testability | 2 | Có expected behavior tương đối rõ; có thể kiểm thử bằng test account và email verification flow, dù cần test data/setup. |
| Security | 4 | Liên quan authentication và thay đổi thuộc tính identity/account; cần kiểm soát authorization và verification để tránh account takeover. |
| Architecture Impact | 2 | Nếu hệ thống đã có account service và email mechanism, thay đổi chủ yếu nằm trong flow hiện có và không nhất thiết tạo architectural decision mới. |

Profile:

```text
(C=3, T=2, S=4, A=2)
```

Ví dụ này minh họa tại sao **không nên gom 4 axis thành một điểm difficulty duy nhất**: requirement có Complexity trung bình nhưng Security cao.

---

# 8. Human Annotation và Requirement Analyzer

Rubric này phục vụ hai nhánh:

### Human Annotation

Human annotators dùng rubric để tạo:

```text
Ground-truth Requirement Profile
```

### Requirement Analyzer

Analyzer LLM nhận cùng requirement và áp dụng **cùng rubric**, tạo:

```text
Predicted Requirement Profile
```

Sau đó có thể so sánh:

- score agreement;
- inter-rater agreement;
- agreement giữa human và Analyzer;
- consistency giữa các lần chạy;
- độ ổn định theo từng axis.

Điều này giữ cho rubric vừa là **annotation guideline**, vừa là **specification cho Requirement Analyzer**.

---


# 9. Tài liệu tham khảo

## 9.1. Requirements Engineering và Requirement Quality

1. **ISO/IEC/IEEE 29148:2018.** *Systems and software engineering — Life cycle processes — Requirements engineering.*  
   Dùng làm chuẩn tham chiếu cho các thuộc tính chất lượng requirement, đặc biệt tính rõ ràng, nhất quán, đầy đủ và **verifiable**.

2. Pohl, K. (2010). *Requirements Engineering: Fundamentals, Principles, and Techniques.* Springer.  
   Tham khảo về chất lượng requirement, elicitation, specification và validation.

3. van Lamsweerde, A. (2009). *Requirements Engineering: From System Goals to UML Models to Formal Specifications.* Wiley.  
   Tham khảo nền tảng về requirements engineering và đặc tả yêu cầu.

4. Femmer, H., Fernández, D. M., Wagner, S., & Eder, S. (2017). *Rapid quality assurance with requirements smells.* Journal of Systems and Software.  
   Tham khảo về requirements smells và tự động hóa kiểm tra chất lượng requirement.

---

## 9.2. Complexity

5. *A Complexity Measure based on Requirement Engineering Document.*  
   Nghiên cứu về việc sử dụng thông tin trong requirements engineering document, bao gồm input/output complexity và functional/non-functional requirements, để đo complexity ở cấp requirement.

6. *Using Software Requirement Specification as Complexity Metric for Multi-Paradigm Programming Languages.*  
   Tham khảo hướng sử dụng SRS/requirement information như một nguồn metric complexity thay vì chỉ dựa vào source-code metrics.

7. *Metrics for Requirements Engineering and Automated Requirements Tools.*  
   Tham khảo các metric ở cấp requirements engineering như size, traceability, completeness và volatility.

---

## 9.3. Security Requirements

8. Swiderski, F., & Snyder, W. (2004). *Threat Modeling.* Microsoft Press.  
   Nguồn tham khảo cho **STRIDE**: Spoofing, Tampering, Repudiation, Information Disclosure, Denial of Service và Elevation of Privilege.

9. Sindre, G., & Opdahl, A. L. (2005). *Eliciting security requirements with misuse cases.* Requirements Engineering, 10(1), 34–44.  
   Tham khảo phương pháp misuse cases trong elicitation security requirements.

10. Faily, S., & Flechais, I. (2011). *Eliciting Usable Security Requirements with Misusability Cases.* IEEE Requirements Engineering Conference.  
    Tham khảo việc sử dụng misusability cases trong security requirements.

11. Chaudhary, S. et al. *STORE: Security Threat Oriented Requirements Engineering Methodology.*  
    Tham khảo threat-oriented requirements engineering và STRIDE categorization.

12. **OWASP Application Security Verification Standard (ASVS).**  
    Tham khảo các yêu cầu và tiêu chí verification cho application security.

13. **OWASP Top 10.**  
    Tham khảo các nhóm rủi ro bảo mật ứng dụng phổ biến khi xây dựng security evidence.

---

## 9.4. Architecture Impact / Architecturally Significant Requirements

14. Chen, L., Ali Babar, M., & Nuseibeh, B. (2013). *Characterizing Architecturally Significant Requirements.* IEEE Software, 30(2), 38–45.  
    Nguồn tham khảo chính cho khái niệm **Architecturally Significant Requirements (ASR)** và các yếu tố như business value, technical risk, stakeholder concerns và quality attributes.

15. Zimmermann, O. (2020). *ASR Test — Seven Criteria for Architectural Significance.*  
    Tham khảo checklist thực dụng để nhận diện architecturally significant requirements.

16. *Architecturally Significant Requirements.*  
    Tài liệu tổng hợp về các indication thường được dùng để nhận diện ASR, bao gồm quality attributes, business value/technical risk và stakeholder concerns.

---

## 9.5. Testability / Verifiability

17. Wilson, W. M., Rosenberg, L. H., & Hyatt, L. E. (1997). *Automated Quality Analysis of Natural Language Requirement Specifications.* Proceedings of the Pacific Northwest Software Quality Conference.  
    Nguồn tham khảo chính cho **weak phrases** trong natural-language requirements và khả năng tự động đánh giá chất lượng requirement.

18. **ISO/IEC/IEEE 29148:2018.**  
    Tham khảo thuộc tính **verifiable**: requirement cần có cách xác nhận khách quan thông qua inspection, test, demonstration hoặc analysis.

19. Femmer, H., Fernández, D. M., Wagner, S., & Eder, S. (2017). *Rapid quality assurance with requirements smells.* Journal of Systems and Software.  
    Tham khảo requirements smells và các vấn đề ảnh hưởng đến chất lượng/verifiability của requirement.

20. *Requirements Quality Research – Terminology and Approaches* (2021).  
    Meta-study về nghiên cứu chất lượng requirements, trong đó verifiability/testability là một thuộc tính chất lượng quan trọng.

---