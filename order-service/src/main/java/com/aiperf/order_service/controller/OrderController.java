package com.aiperf.order_service.controller;

import com.aiperf.order_service.entity.Order;
import com.aiperf.order_service.repository.OrderRepository;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
public class OrderController {

    private final OrderRepository repository;

    public OrderController(OrderRepository repository) {
        this.repository = repository;
    }

    @GetMapping("/orders")
    public List<Order> getOrders() {
        return repository.findAll();
    }

    @GetMapping("/orders/{id}")
    public Order getOrder(@PathVariable Integer id) {
        return repository.findById(id).orElse(null);
    }
}