package com.aiperf.order_service.entity;

import jakarta.persistence.*;
import lombok.Data;

@Data
@Entity
@Table(name = "orders")
public class Order {

    @Id
    private Integer id;

    @Column(name = "user_id")
    private Integer userId;

    @Column(name = "product_id")
    private Integer productId;

    private Integer quantity;
}