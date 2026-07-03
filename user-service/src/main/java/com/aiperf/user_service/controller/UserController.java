package com.aiperf.user_service.controller;

import com.aiperf.user_service.entity.User;
import com.aiperf.user_service.repository.UserRepository;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
public class UserController {

    private final UserRepository repository;

    public UserController(UserRepository repository) {
        this.repository = repository;
    }

    @GetMapping("/users")
    public List<User> getUsers() {
        return repository.findAll();
    }

    @GetMapping("/users/{id}")
    public User getUser(@PathVariable Integer id) {
    return repository.findById(id).orElse(null);
    }
}