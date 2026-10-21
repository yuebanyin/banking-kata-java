package com.optivem.kata.banking.adapter.driver.restapi.spring;

import com.optivem.kata.banking.core.ports.driven.AccountStorageRoute;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.HandlerInterceptor;
import org.springframework.web.servlet.config.annotation.InterceptorRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

/** Routes explicitly marked baseline probes through PostgreSQL only. */
@Configuration
public class EvidenceSourceRoutingConfiguration implements WebMvcConfigurer {
    static final String HEADER = "X-TB4-Source-Only";

    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new HandlerInterceptor() {
            @Override
            public boolean preHandle(HttpServletRequest request, HttpServletResponse response,
                                     Object handler) {
                if ("true".equalsIgnoreCase(request.getHeader(HEADER))) {
                    AccountStorageRoute.useSourceOnly();
                }
                return true;
            }

            @Override
            public void afterCompletion(HttpServletRequest request, HttpServletResponse response,
                                        Object handler, Exception exception) {
                AccountStorageRoute.clear();
            }
        }).addPathPatterns("/bank-accounts", "/bank-accounts/**");
    }
}
