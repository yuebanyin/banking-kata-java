FROM gradle:7.4.1-jdk17 AS build
WORKDIR /workspace
COPY . .
RUN ./gradlew --no-daemon :startup:bootJar

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=build /workspace/startup/build/libs/*.jar /app/banking.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/banking.jar"]
